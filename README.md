# Picklepals

A simple social app for pickleball players: find courts on a map, check in, add
friends, chat, schedule casual or ranked games, and climb the rankings.

## Stack

- **Backend** — Flask + SQLAlchemy (SQLite by default), JWT auth. All endpoints under `/api`.
- **Frontend** — single-page mobile-first web app in `frontend/` (vanilla JS + Leaflet),
  served directly by Flask at `/`.

## Quick start

```bash
pip install -r requirements.txt

# Import court data (≈18.5k courts) and create demo users/games
python3 -m backend.seed --courts-dir "../pickleball court web scraper/output" --demo

# Run the app
python3 -c "from backend.app import app; app.run(port=8000)"
# open http://localhost:8000
```

Demo accounts: `dana@example.com`, `marcus@example.com`, `priya@example.com`,
`tom@example.com` — password `pickleball`.

## Features

- **Courts** — map of all imported courts with live "players here" counts, search,
  court detail (photos, amenities, fees, open-play schedule), one-tap check-in with
  "looking for a game" flag.
- **Play** — nearby upcoming games feed, schedule a game at any court (casual or
  ranked), join/leave, record scores. Casual scores finalize instantly; ranked
  scores need an opposing player's one-tap confirmation (auto-confirms after 24h,
  disputes clear the score for re-entry) before ELO ratings move (K=32,
  team-average for doubles). Includes a Results feed of finished games, win
  streaks, and a leaderboard. The app polls every 12s, so score confirmations,
  game invites, and joins pop up as toasts with action badges on the Play tab.
- **People** — player search, friend requests, friends list with live presence
  ("At Larson Park · wants to play"), 1:1 chat with unread badges.
- **Profile** — rating / record / win rate, match history with rating deltas,
  editable profile (skill level, bio, avatar color, home court), activity feed.

## Tests

```bash
python3 -m pytest tests/
```

## Project layout

```
backend/
  app.py            Flask bootstrap, serves frontend + /api blueprints
  models.py         User, Court, CheckIn, Friendship, Message, Game, GamePlayer, Notification
  routes/           auth, courts, games, social, chat
  seed.py           court data importer + demo seed
frontend/           index.html, styles.css, app.js (no build step)
tests/test_api.py   end-to-end API tests
```
