"""Stash Buster – a Ravelry-powered yarn stash pattern suggester.

Authentication strategy:
  - OAuth 2.0 is used ONLY for login (to identify the Ravelry user).
  - Basic Auth (app-level API keys) is used for ALL data reads
    (stash, projects, pattern search) because OAuth tokens lack the
    necessary scope for user-data endpoints.
"""

import os
import secrets
import time
from functools import wraps
from urllib.parse import urlencode

import requests as http
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from requests_oauthlib import OAuth2Session

from ravelry import RavelryClient

API_BASE = "https://api.ravelry.com"

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────

# OAuth 2.0 – for login only
CLIENT_ID = os.environ.get("RAVELRY_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("RAVELRY_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("REDIRECT_URI", "https://localhost:5001/callback")

AUTHORIZATION_URL = "https://www.ravelry.com/oauth2/auth"
TOKEN_URL = "https://www.ravelry.com/oauth2/token"

# Basic Auth – for API data reads
RAVELRY_ACCESS_KEY = os.environ.get("RAVELRY_ACCESS_KEY", "")
RAVELRY_PERSONAL_KEY = os.environ.get("RAVELRY_PERSONAL_KEY", "")

NEEDS_SETUP = (
    not CLIENT_ID or not CLIENT_SECRET
    or not RAVELRY_ACCESS_KEY or not RAVELRY_PERSONAL_KEY
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))

# Allow OAuth over plain HTTP for local development
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


# ── Helpers ─────────────────────────────────────────────────────────────

def get_client():
    """Build a RavelryClient using the app's Basic Auth credentials."""
    return RavelryClient(RAVELRY_ACCESS_KEY, RAVELRY_PERSONAL_KEY)


def login_required(f):
    """Decorator that redirects to the login page when unauthenticated."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


def get_profile(client, username):
    """Fetch projects and build a preference profile for the user."""
    projects = client.get_full_projects(username)
    return projects, client.analyze_project_history(projects)


def collect_filters():
    """Read filter values from query string parameters."""
    return {
        "query": request.args.get("query", "").strip(),
        "availability": request.args.get("availability", ""),
        "sort": request.args.get("sort", ""),
        "diff_min": request.args.get("diff_min", ""),
        "diff_max": request.args.get("diff_max", ""),
        "craft": request.args.get("craft", ""),
        "pc": request.args.get("pc", ""),
    }


@app.context_processor
def utility_functions():
    """Make helper functions available in all templates."""

    def filter_url(base_url, filters, page=None):
        """Build a URL that preserves current filter values."""
        params = {k: v for k, v in (filters or {}).items() if v}
        if page is not None:
            params["page"] = page
        if not params:
            return base_url
        return f"{base_url}?{urlencode(params)}"

    return {"filter_url": filter_url}


# ── Auth routes ─────────────────────────────────────────────────────────

@app.route("/login")
def login():
    """Start the OAuth 2.0 authorization flow (for user identity only)."""
    if NEEDS_SETUP:
        return redirect(url_for("index"))
    oauth = OAuth2Session(CLIENT_ID, redirect_uri=REDIRECT_URI, scope=["offline"])
    auth_url, state = oauth.authorization_url(AUTHORIZATION_URL)
    session["oauth_state"] = state
    return redirect(auth_url)


@app.route("/callback")
def callback():
    """Handle the OAuth 2.0 redirect – store token + username in session."""
    try:
        oauth = OAuth2Session(
            CLIENT_ID,
            redirect_uri=REDIRECT_URI,
            state=session.get("oauth_state"),
        )
        token = oauth.fetch_token(
            TOKEN_URL,
            client_secret=CLIENT_SECRET,
            authorization_response=request.url,
        )

        resp = http.get(
            f"{API_BASE}/current_user.json",
            headers={"Authorization": f"Bearer {token['access_token']}"},
        )
        resp.raise_for_status()
        username = resp.json().get("user", {}).get("username")

        if not username:
            return f"<h2>Login failed</h2><p>Could not get username. API response: {resp.json()}</p>", 500

        # Keep the token so we can make writes on behalf of this user
        session["username"] = username
        session["oauth_token"] = token
        return redirect(url_for("stash"))

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        return f"<h2>OAuth callback error</h2><pre>{tb}</pre>", 500


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ── Page routes ─────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Landing page."""
    if NEEDS_SETUP:
        return render_template("setup.html")
    if "username" in session:
        return redirect(url_for("stash"))
    return render_template("index.html")


@app.route("/stash")
@login_required
def stash():
    """Display the user's yarn stash."""
    client = get_client()
    username = session["username"]
    stash_items = client.get_full_stash(username)
    return render_template("stash.html", stash=stash_items, username=username)


@app.route("/suggestions/stash/<int:stash_id>")
@login_required
def suggestions_for_yarn(stash_id):
    """Suggest patterns that match a specific stash yarn, informed by
    project history preferences and user-applied filters."""
    client = get_client()
    username = session["username"]
    page = request.args.get("page", 1, type=int)
    filters = collect_filters()

    # Fetch stash item details
    stash_data = client.get_stash_item(username, stash_id)
    stash_item = stash_data.get("stash", {})

    # Build user preference profile from project history
    projects = client.get_full_projects(username)
    profile = client.analyze_project_history(projects)

    # Collect the set of pattern ids the user has already made so we can
    # exclude them from favorite-based suggestions.
    made_pattern_ids = {
        proj.get("pattern_id") for proj in projects if proj.get("pattern_id")
    }

    # Favorites that match this yarn, excluding already-made patterns
    favorite_matches = []
    try:
        favorites = client.get_full_favorites(username, types="pattern")
        favorite_matches = client.filter_favorites_for_yarn(
            favorites, stash_item, made_pattern_ids=made_pattern_ids, limit=12
        )
    except Exception:
        favorite_matches = []

    # General suggestions for this yarn
    results = client.suggest_patterns_for_stash_item(
        stash_item, profile=profile, filters=filters, page=page
    )
    patterns = results.get("patterns", [])
    paginator = results.get("paginator", {})

    # Smart suggestions: yarn properties + user's favorite categories
    smart_groups = client.suggest_patterns_for_stash_smart(
        stash_item, profile, filters=filters
    )

    return render_template(
        "suggestions.html",
        patterns=patterns,
        paginator=paginator,
        smart_groups=smart_groups,
        favorite_matches=favorite_matches,
        profile=profile,
        filters=filters,
        title=f"Patterns for: {stash_item.get('name', 'your yarn')}",
        stash_item=stash_item,
        suggestion_type="stash",
        stash_id=stash_id,
        page=page,
    )


@app.route("/suggestions/projects")
@login_required
def suggestions_from_projects():
    """Suggest patterns across top categories from project history."""
    client = get_client()
    username = session["username"]
    page = request.args.get("page", 1, type=int)
    filters = collect_filters()

    projects = client.get_full_projects(username)
    profile, category_groups = client.suggest_patterns_from_projects(
        projects, filters=filters, page=page
    )

    return render_template(
        "project_suggestions.html",
        category_groups=category_groups,
        profile=profile,
        filters=filters,
        page=page,
    )


# ── Favorites API (user-scoped via OAuth) ───────────────────────────────

def _current_oauth_token():
    """Return the session's OAuth token, refreshing it if it's expired."""
    token = session.get("oauth_token")
    if not token:
        return None

    expires_at = token.get("expires_at", 0)
    if expires_at and expires_at < time.time() + 30:
        refresh_token = token.get("refresh_token")
        if not refresh_token:
            return None
        oauth = OAuth2Session(CLIENT_ID)
        new_token = oauth.refresh_token(
            TOKEN_URL,
            refresh_token=refresh_token,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )
        session["oauth_token"] = new_token
        return new_token

    return token


@app.route("/favorite/pattern/<int:pattern_id>", methods=["POST"])
@login_required
def favorite_pattern(pattern_id):
    """Add a pattern to the logged-in user's Ravelry favorites.

    Uses the user's OAuth access token so the favorite lands on the account
    that is currently signed in (not the app-key owner's account).
    """
    token = _current_oauth_token()
    if not token:
        return jsonify({"ok": False, "error": "Please log in again."}), 401

    username = session["username"]
    try:
        resp = http.post(
            f"{API_BASE}/people/{username}/favorites/create.json",
            headers={"Authorization": f"Bearer {token['access_token']}"},
            data={"type": "pattern", "favorited_id": pattern_id},
        )
        resp.raise_for_status()
        return jsonify({"ok": True})
    except http.HTTPError as e:
        body = getattr(e.response, "text", "")[:200]
        return jsonify({"ok": False, "error": f"{e}: {body}"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


# ── Run ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))

    # Use local SSL certs only when running locally
    ssl_ctx = None
    if os.path.exists("cert.pem") and os.path.exists("key.pem"):
        ssl_ctx = ("cert.pem", "key.pem")

    app.run(debug=True, port=port, ssl_context=ssl_ctx)
