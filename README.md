# Stash Buster

A local web app that connects to your Ravelry account, shows your yarn stash, and suggests patterns to help you use it up.

## Setup

### 1. Register a Ravelry API app

1. Log in to [Ravelry](https://www.ravelry.com)
2. Go to [https://www.ravelry.com/pro/developer](https://www.ravelry.com/pro/developer)
3. Click **"create new app"**
4. Fill in the form:
   - **App Name:** Stash Buster (or anything you like)
   - **App Description:** Personal stash pattern suggester
   - **App URL:** `https://localhost:5001`
   - **Callback URL:** `https://localhost:5001/callback`
   - **Access type:** Select **OAuth 2.0**
5. Save and copy the **Client ID** and **Client Secret**

### 2. Configure environment variables

```bash
cd stash-buster
cp .env.example .env
```

Edit `.env` and paste in your credentials:

```
RAVELRY_CLIENT_ID=your_client_id_here
RAVELRY_CLIENT_SECRET=your_client_secret_here
FLASK_SECRET_KEY=any-random-string-you-want
```

### 3. Install dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Run the app

```bash
python app.py
```

Open [https://localhost:5001](https://localhost:5001) in your browser and click **"Connect with Ravelry"** to get started.

## Features

- **Browse your stash** — See all yarns with photos, weight, yardage, and color
- **Pattern suggestions per yarn** — Click any yarn to find patterns that match its weight and fit within available yardage
- **Project-based suggestions** — Get pattern ideas based on the types of projects you've already made
- **Pagination** — Browse through many results across multiple pages

## Project structure

```
stash-buster/
├── app.py              # Flask routes and OAuth flow
├── ravelry.py          # Ravelry API client
├── requirements.txt    # Python dependencies
├── .env.example        # Template for credentials
├── templates/
│   ├── base.html       # Shared layout
│   ├── index.html      # Landing page
│   ├── stash.html      # Stash browser
│   └── suggestions.html # Pattern results
└── static/
    └── style.css       # Styling
```
