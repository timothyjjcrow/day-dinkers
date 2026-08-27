# Third Shot

A social app for pickleball players, built to work across the US: find courts on a
map, discover players near you, check in, add friends, chat, schedule casual or
ranked (and recurring) games, and climb the rankings.

## Stack

- **Backend** — Flask + SQLAlchemy (SQLite for local dev, Postgres in production),
  JWT auth. All endpoints under `/api`.
- **Frontend** — single-page mobile-first web app in `frontend/` (vanilla JS +
  Leaflet, no build step), served directly by Flask at `/`. Installable PWA.

## Quick start

```bash
pip install -r requirements.txt

# Import court data (~18.5k courts) and create demo users/games.
# Courts are bundled at data/courts.json.gz, so either source works:
python3 -m backend.seed --courts-file data/courts.json.gz --demo
#   …or re-import from the scraper output:
# python3 -m backend.seed --courts-dir "../pickleball court web scraper/output" --demo

# Run the app
python3 -c "from backend.app import app; app.run(port=8000)"
# open http://localhost:8000
```

Demo accounts: `dana@example.com`, `marcus@example.com`, `priya@example.com`,
`tom@example.com` — password `pickleball`.

## Features

- **Courts & map** — clustered map of ~18.5k US courts with live "players here"
  counts, amenity filters (lighted/indoor/playing-now), text search, and
  **geocoding area search** ("jump to Austin, TX"). Court detail has a pinned
  hero (photos, amenities, fees, open-play hours), one-tap check-in, court chat,
  and share links. Mobile results render progressively (8 on the map, then
  20-at-a-time in List), and selecting a court no longer collapses the browsing
  context while exposing Details, Play here, and Directions. Auto check-in by
  proximity when the app is open.
- **Location** — first-run onboarding sets a **home area**; the map and feeds
  open there. **Players Near You** discovery (by last check-in / home court)
  with skill filter and add-friend / message / challenge actions.
- **Play** — action-first mobile home with **Play now / Plan ahead**, a guided
  Where → When → Who rally planner with smart court/time/friend defaults, nearby
  games feed, and scheduling at any court (casual or ranked),
  **recurring weekly open-play sessions**, join/leave, and an active-game banner.
  Casual scores finalize instantly; ranked scores need an opposing player's
  one-tap confirmation (auto-confirm after 24h; disputes clear for re-entry)
  before ELO moves (K=32, team-average for doubles). Results feed, win streaks,
  and a podium leaderboard. **Game visibility**: open (anyone nearby) /
  friends / private (specific invitees). Challenges create private 1v1s.
- **Compete** — mobile-first box leagues and single-elimination tournaments,
  with registration, seeding, round/bracket progression, role-aware action
  queues, and one shared score → confirm → dispute → resolve result workflow.
  Exact match links reopen the right result (including older league rounds),
  while live refresh preserves scroll and form context.
- **People** — player search, friend requests, friends list with live presence,
  per-section attention badges, and a single recency-sorted Community inbox for
  DMs plus court, club, game, league, and tournament rooms—including active
  competition rooms before the first message. Chat is mobile-keyboard-aware
  across every room. Every text/photo send enters a durable, account-scoped
  outbox first, retries safely after offline/reload failures, and remains
  visibly retryable or removable without duplicate delivery.
- **Profile** — rating / record / streak, match history with rating deltas,
  editable profile (photo, skill level, bio, avatar color, home court/area),
  activity feed, install-to-home-screen hint.
- **Realtime + offline resilience** — polling surfaces confirmations, invites,
  joins, chat, and challenges as toasts/badges plus optional system
  notifications. Returning players see their token-scoped private snapshot
  immediately while `/me` revalidates in the background, so a weak connection
  never creates a blank launch. The installable app shell remains useful
  offline; routed notifications and queued messages reconcile cleanly when
  connectivity returns.

## Production / deployment (Render)

Deployed as a single Python web service (`render.yaml`):
`gunicorn --workers 1 --threads 8 --bind 0.0.0.0:$PORT backend.wsgi:app`.
On first boot the app auto-creates the schema, runs additive migrations, and
seeds the bundled courts in a background thread.

Required environment variables:

| Var | Purpose |
|-----|---------|
| `APP_ENV` | `production` (enables prod config; the app refuses to boot if `SECRET_KEY` is unset/default) |
| `SECRET_KEY` | strong random value for JWT signing (Render can generate it) |
| `DATABASE_URL` | Postgres URL (`postgres://…` is normalized automatically); falls back to SQLite if unset |
| `AUTO_SEED_COURTS` | `true` to seed bundled courts on first boot |
| `AUTO_CREATE_DB` | `true` to create tables on boot (default true) |

Optional: `RATE_LIMIT_ENABLED` (default true), `RESET_DB_ON_BOOT` (one-time
schema reset escape hatch — set, deploy once, then remove).

Hardening in place: production secret-key guard, per-IP rate limiting on auth and
write endpoints, and security headers (nosniff, SAMEORIGIN, Referrer-Policy).
The app keeps its tables in a dedicated `picklepals` Postgres schema so it never
collides with other tables in the database.

Not yet wired (optional follow-up): web push notifications require VAPID keys to
be provisioned as env vars.

## Tests

```bash
python3 -m pytest tests/
```

## Project layout

```
backend/
  app.py            Flask bootstrap, serves frontend + /api blueprints, migrations
  config.py         env-driven config (dev / staging / production / testing)
  models.py         User, Court, CheckIn, Friendship, Message, Game, GamePlayer,
                    GameInvite, FavoriteCourt, Notification
  security.py       in-memory per-IP rate limiter
  routes/           auth, courts (+ geocode), games, social (+ players/nearby), chat
  seed.py           court data importer (dir or bundled .json.gz) + demo seed
  wsgi.py           gunicorn entrypoint (backend.wsgi:app)
data/courts.json.gz bundled court dataset for first-boot seeding
frontend/           index.html, styles.css, app.js, manifest, sw.js (no build step)
tests/test_api.py   end-to-end API tests
```
