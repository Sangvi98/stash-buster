"""Ravelry API client for Stash Buster.

Uses Basic Auth (app credentials) for all data reads.
OAuth 2.0 is handled separately in app.py just for user login.
"""

import requests

API_BASE = "https://api.ravelry.com"


class RavelryClient:
    """Wrapper around the Ravelry API using Basic Auth (read-only)."""

    def __init__(self, access_key, personal_key):
        self.session = requests.Session()
        self.session.auth = (access_key, personal_key)
        self.session.headers.update({"Accept": "application/json"})

    def _get(self, path, params=None):
        resp = self.session.get(f"{API_BASE}{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    # ── User ────────────────────────────────────────────────────────────

    def current_user(self):
        """Return the authenticated user's profile info."""
        return self._get("/current_user.json")

    # ── Stash ───────────────────────────────────────────────────────────

    def get_stash(self, username, page=1, page_size=100):
        """Return a page of the user's yarn stash."""
        return self._get(
            f"/people/{username}/stash/list.json",
            params={"page": page, "page_size": page_size, "sort": "recent"},
        )

    def get_full_stash(self, username):
        """Fetch all stash pages and return the combined list of items."""
        items = []
        page = 1
        while True:
            data = self.get_stash(username, page=page, page_size=100)
            stash_items = data.get("stash", [])
            items.extend(stash_items)
            paginator = data.get("paginator", {})
            if page >= paginator.get("last_page", 1):
                break
            page += 1
        return items

    def get_stash_item(self, username, stash_id):
        """Return details for a single stash entry."""
        return self._get(f"/people/{username}/stash/{stash_id}.json")

    # ── Projects ────────────────────────────────────────────────────────

    def get_projects(self, username, page=1, page_size=100):
        """Return a page of the user's projects."""
        return self._get(
            f"/people/{username}/projects/list.json",
            params={"page": page, "page_size": page_size, "sort": "completed_"},
        )

    def get_full_projects(self, username):
        """Fetch all project pages and return the combined list."""
        items = []
        page = 1
        while True:
            data = self.get_projects(username, page=page, page_size=100)
            projects = data.get("projects", [])
            items.extend(projects)
            paginator = data.get("paginator", {})
            if page >= paginator.get("last_page", 1):
                break
            page += 1
        return items

    # ── Pattern search ──────────────────────────────────────────────────

    def search_patterns(self, **kwargs):
        """Search Ravelry patterns. Accepts any valid search parameter."""
        return self._get("/patterns/search.json", params=kwargs)

    def get_pattern(self, pattern_id):
        """Return full details for a single pattern."""
        return self._get(f"/patterns/{pattern_id}.json")

    # ── Pattern detail fetching ───────────────────────────────────────

    def enrich_projects_with_patterns(self, projects):
        """Fetch full pattern details for each project and attach them.

        The project list endpoint only returns pattern_id, not the full
        pattern object.  We fetch each unique pattern once and cache it.
        """
        pattern_cache = {}
        for proj in projects:
            pid = proj.get("pattern_id")
            if not pid:
                continue
            if pid not in pattern_cache:
                try:
                    data = self.get_pattern(pid)
                    pattern_cache[pid] = data.get("pattern", {})
                except Exception:
                    pattern_cache[pid] = {}
            proj["pattern"] = pattern_cache[pid]
        return projects

    # ── Profile analysis ────────────────────────────────────────────────

    def analyze_project_history(self, projects):
        """Extract preferences from the user's project history.

        Returns a dict with:
          - craft: most-used craft type (e.g. "knitting")
          - top_categories: list of (permalink, name, count) tuples, sorted
            by frequency, up to 5
          - avg_difficulty: average difficulty across projects that have a
            linked pattern with a difficulty rating
          - difficulty_range: (min, max) difficulty the user has done
        """
        # Enrich projects with full pattern data first
        self.enrich_projects_with_patterns(projects)

        craft_counts = {}
        category_counts = {}
        category_names = {}
        difficulties = []

        for proj in projects:
            # Craft type (available directly on the project)
            craft = proj.get("craft_name")
            if craft:
                craft_counts[craft.lower()] = craft_counts.get(craft.lower(), 0) + 1

            # Pattern info (now enriched with full details)
            pattern = proj.get("pattern") or {}

            # Difficulty
            diff = pattern.get("difficulty_average")
            if diff and diff > 0:
                difficulties.append(diff)

            # Categories
            categories = pattern.get("pattern_categories") or []
            for cat in categories:
                pc = cat.get("permalink")
                if pc:
                    category_counts[pc] = category_counts.get(pc, 0) + 1
                    category_names[pc] = cat.get("name", pc)

        # Sort categories by count descending
        sorted_cats = sorted(category_counts.items(), key=lambda x: x[1], reverse=True)
        top_categories = [
            (pc, category_names.get(pc, pc), count)
            for pc, count in sorted_cats[:5]
        ]

        # Most common craft
        craft = max(craft_counts, key=craft_counts.get) if craft_counts else None

        # Difficulty stats
        avg_difficulty = sum(difficulties) / len(difficulties) if difficulties else None
        difficulty_range = (min(difficulties), max(difficulties)) if difficulties else None

        return {
            "craft": craft,
            "top_categories": top_categories,
            "avg_difficulty": avg_difficulty,
            "difficulty_range": difficulty_range,
        }

    # ── Filter helpers ─────────────────────────────────────────────────

    @staticmethod
    def apply_user_filters(params, filters):
        """Merge user-supplied filters into a Ravelry search params dict.

        Accepted filter keys (all optional):
          - query:       free-text search string
          - availability: "free", "ravelry", "online" or "" (any)
          - sort:        "best", "popularity", "rating", "date", "difficulty"
          - diff_min:    minimum difficulty (1-10)
          - diff_max:    maximum difficulty (1-10)
          - craft:       "knitting", "crochet", or "" (any)
          - pc:          pattern category permalink
        """
        if not filters:
            return params

        if filters.get("query"):
            params["query"] = filters["query"]

        if filters.get("availability"):
            avail = filters["availability"]
            if avail == "free":
                params["availability"] = "free"
            elif avail == "online":
                params["availability"] = "online"
            elif avail == "ravelry":
                params["availability"] = "ravelry"

        if filters.get("sort"):
            params["sort"] = filters["sort"]

        # Difficulty range – override profile-based defaults
        diff_min = filters.get("diff_min")
        diff_max = filters.get("diff_max")
        if diff_min or diff_max:
            lo = int(diff_min) if diff_min else 1
            hi = int(diff_max) if diff_max else 10
            params["diff"] = f"{lo}|{hi}"

        if filters.get("craft"):
            params["craft"] = filters["craft"]

        if filters.get("pc"):
            params["pc"] = filters["pc"]

        return params

    # ── Smart suggestions ───────────────────────────────────────────────

    def suggest_patterns_for_stash_item(self, stash_item, profile=None,
                                         filters=None, page=1, page_size=12):
        """Suggest patterns matching a stash yarn, optionally informed by
        the user's project history profile and user-applied filters."""
        params = {"page": page, "page_size": page_size, "sort": "best"}

        # Match yarn weight
        yarn = stash_item.get("yarn") or {}
        yarn_weight = yarn.get("yarn_weight") or {}
        weight_name = yarn_weight.get("name")
        if weight_name:
            params["weight"] = weight_name.lower().replace(" ", "-")

        # Filter by available yardage
        total_yards = stash_item.get("yards")
        if total_yards and total_yards > 0:
            params["yardage-to"] = int(total_yards)

        # Craft type: prefer stash item's, fall back to profile
        craft = stash_item.get("craft_name")
        if not craft and profile:
            craft = profile.get("craft")
        if craft:
            params["craft"] = craft.lower()

        # Use profile difficulty range to keep suggestions within comfort zone
        if profile and profile.get("difficulty_range"):
            diff_min, diff_max = profile["difficulty_range"]
            params["diff"] = f"{max(1, diff_min - 1)}|{min(10, diff_max + 1)}"

        # Apply user filters last so they override defaults
        self.apply_user_filters(params, filters)

        return self.search_patterns(**params)

    def suggest_patterns_for_stash_smart(self, stash_item, profile,
                                          filters=None, page_size=6):
        """Combined smart suggestions: patterns matching this yarn that also
        align with the user's favorite categories."""
        results = []

        yarn = stash_item.get("yarn") or {}
        yarn_weight = yarn.get("yarn_weight") or {}
        weight_name = yarn_weight.get("name")
        total_yards = stash_item.get("yards")

        craft = stash_item.get("craft_name")
        if not craft and profile:
            craft = profile.get("craft")

        # If the user filtered to a specific category, only show that one
        filter_pc = (filters or {}).get("pc")
        cats_to_search = (profile or {}).get("top_categories", [])[:3]
        if filter_pc:
            cats_to_search = [
                (pc, name, c) for pc, name, c in cats_to_search if pc == filter_pc
            ]
            # If the filtered category isn't in top 3, search it anyway
            if not cats_to_search:
                cats_to_search = [(filter_pc, filter_pc.replace("-", " ").title(), 0)]

        for pc, cat_name, _count in cats_to_search:
            params = {"page": 1, "page_size": page_size, "sort": "best", "pc": pc}
            if weight_name:
                params["weight"] = weight_name.lower().replace(" ", "-")
            if total_yards and total_yards > 0:
                params["yardage-to"] = int(total_yards)
            if craft:
                params["craft"] = craft.lower()
            if profile and profile.get("difficulty_range"):
                diff_min, diff_max = profile["difficulty_range"]
                params["diff"] = f"{max(1, diff_min - 1)}|{min(10, diff_max + 1)}"

            # Apply user filters (may override sort, difficulty, etc.)
            self.apply_user_filters(params, filters)

            search_results = self.search_patterns(**params)
            patterns = search_results.get("patterns", [])
            if patterns:
                results.append({"category": cat_name, "patterns": patterns})

        return results

    def suggest_patterns_from_projects(self, projects, filters=None,
                                        page=1, page_size=8):
        """Suggest patterns across the user's top categories from project
        history, factoring in difficulty comfort zone and user filters."""
        profile = self.analyze_project_history(projects)
        results = []

        cats = profile.get("top_categories", [])[:3]

        # If user filtered to a specific category, only show that
        filter_pc = (filters or {}).get("pc")
        if filter_pc:
            cats = [(pc, n, c) for pc, n, c in cats if pc == filter_pc]
            if not cats:
                cats = [(filter_pc, filter_pc.replace("-", " ").title(), 0)]

        for pc, cat_name, count in cats:
            params = {
                "page": page,
                "page_size": page_size,
                "sort": "best",
                "pc": pc,
            }
            if profile.get("craft"):
                params["craft"] = profile["craft"]
            if profile.get("difficulty_range"):
                diff_min, diff_max = profile["difficulty_range"]
                params["diff"] = f"{max(1, diff_min - 1)}|{min(10, diff_max + 1)}"

            self.apply_user_filters(params, filters)

            search_results = self.search_patterns(**params)
            patterns = search_results.get("patterns", [])
            results.append({
                "category": cat_name,
                "count": count,
                "patterns": patterns,
            })

        return profile, results
