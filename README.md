# Third Shot

A social app for pickleball players, built to work across the US: find courts on a
map, discover players near you, check in, add friends, chat, schedule casual or
ranked (and recurring) games, and climb the rankings.

## Stack

- **Backend** — Flask + SQLAlchemy (SQLite for local dev, Postgres in production),
  JWT auth. All endpoints under `/api`.
- **Frontend** — single-page mobile-first web app in `public/` (vanilla JS +
  Leaflet, no build step), served by Vercel's CDN and by Flask locally.
  Installable PWA.

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

## Free, no-card deployment (Vercel Hobby + Neon Free)

The deployment uses a Vercel Hobby project and a Neon Free PostgreSQL database,
both in `iad1` / AWS N. Virginia. Neon explicitly requires no credit card. The
Vercel Hobby plan is free and a card is requested when upgrading to Pro; Vercel
does not make an unconditional promise that it will never ask an account for
verification. Hobby is restricted to personal, non-commercial use. It pauses
instead of billing when a free usage limit is exceeded.

The serverless runtime deliberately disables database DDL, court seeding, and
the in-process web-push worker. Rate limits use an atomic PostgreSQL table shared
by every function instance. Static PWA assets live in `public/` and are served
from Vercel's CDN without invoking Flask.

### Recover the preserved database first

The primary deployment source is the preserved SQLite database at
`instance/recovered-2026-07-07.db`. **Do not start the web service, import the
bundled court seed, or otherwise connect the application to the new PostgreSQL
database before this migration.** App startup performs schema writes and can
start background seeding, while the recovery script requires exclusive access
and intentionally refuses to copy into a target that already contains rows.
The snapshot contains 19 accounts (15 non-demo), 18,510 courts, and 63 games;
its newest recorded user activity is July 7, 2026, so it cannot recover later
Render activity.

1. Install the Python requirements locally, then validate the preserved database.
   The command checks SQLite integrity and applies the current additive migrations
   to a temporary copy; it never changes the preserved file or contacts Postgres.

   ```bash
   pip install -r requirements.txt
   python3 scripts/migrate_sqlite_recovery.py \
     --source instance/recovered-2026-07-07.db \
     --check-only
   ```

   Continue only after it prints `Source recovery check passed` with table and
   row totals.

2. Create/link the Vercel project and provision Neon from Vercel Marketplace.
   Select the `free_v3` plan, `iad1`, and disable Neon Auth because this app has
   its own authentication. Connect the resource to production and development,
   but not Preview (a preview must never mutate the recovered production data).

   ```bash
   npx vercel@59.9.1 link
   npx vercel@59.9.1 integration add neon \
     --plan free_v3 --metadata region=iad1 --metadata auth=false \
     --environment production --environment development
   npx vercel@59.9.1 env pull .env.local --environment=development --yes
   ```

3. Copy Neon's **direct/unpooled** connection URL from the pulled local
   variables into the shell without printing it or placing it in command
   history. Do not use the hostname containing `-pooler` for this one-time
   migration. Never put the URL in a tracked file, chat message, or screenshot.

   ```bash
   read -rs TARGET_DATABASE_URL
   export TARGET_DATABASE_URL
   python3 scripts/migrate_sqlite_recovery.py \
     --source instance/recovered-2026-07-07.db
   unset TARGET_DATABASE_URL
   ```

   The script rejects a pooled target, creates the dedicated `picklepals`
   schema, copies all tables while preserving primary keys and historical
   timestamps, repairs sequences, verifies target row counts, exact primary-key
   sets and foreign-key integrity, and persists the role's runtime
   `search_path`. Success ends with `Migration completed and verified`.
   If it reports that the target is not empty, do not bypass the safeguard;
   delete/recreate the empty Neon resource rather than weakening the check.

4. Confirm Vercel's production `DATABASE_URL` is Neon's pooled URL (its hostname
   contains `-pooler`). Add the runtime variables below as production variables,
   make `SECRET_KEY` sensitive, and leave `RESET_DB_ON_BOOT` completely unset.

5. Deploy production with `npx vercel@59.9.1 --prod`, then verify `/health`
   reports `status: ok`, `db: true`, and `env: production`. Sign in with a
   recovered account and spot-check court search, profiles, friendships, games,
   and messages. These checks supplement the migration's exact verification.

### Explicit fallback: start over with a fresh database

Use a fresh database only after deliberately deciding that
`instance/recovered-2026-07-07.db` is unusable or its recovered data is not
wanted. This fallback loses the recovered accounts, friendships, games, chat,
and other history.

1. Create a separate empty Neon database; do not reuse a partially populated
   recovery target.
2. Initialize it outside the Vercel runtime with the direct connection. Runtime
   schema management remains disabled.
3. Import the bundled courts deliberately, then verify account creation, court
   search, and the map before allowing normal use.

Required environment variables:

| Var | Purpose |
|-----|---------|
| `APP_ENV` | `production` (enables prod config; the app refuses to boot if `SECRET_KEY` is unset/default) |
| `SECRET_KEY` | a new random value of at least 32 bytes for JWT signing |
| `DATABASE_URL` | Neon pooled runtime URL; required in production and normalized to psycopg automatically |
| `SERVERLESS_RUNTIME` | `true` |
| `SCHEMA_MANAGEMENT_ENABLED` | `false`; all DDL belongs in the one-time migration |
| `AUTO_CREATE_DB` | `false` |
| `AUTO_SEED_COURTS` | `false` for the recovered database |
| `RATE_LIMIT_BACKEND` | `database` |
| `PUSH_DELIVERY_ENABLED` | `false`; in-app notifications remain available |

Optional: `RATE_LIMIT_ENABLED` (default true). Never set `RESET_DB_ON_BOOT` on
the recovered database. Web push stays off because a daemon queue cannot
reliably finish inside a stateless function; re-enable it only after adding a
durable outbox/worker.

Neon Free currently includes 100 CU-hours per project each month, 0.5 GB of
storage, 5 GB of egress, scale-to-zero after five idle minutes, and a six-hour
restore window. Keep independent backups of production data; six hours is not a
long-term backup policy.

Official references: [Vercel Flask](https://vercel.com/kb/guide/ship-a-flask-app-on-vercel),
[Vercel Hobby](https://vercel.com/docs/plans/hobby),
[Vercel Function limits](https://vercel.com/docs/functions/limitations),
[Neon pricing](https://neon.com/pricing), and
[Neon connection pooling](https://neon.com/docs/connect/connection-pooling).

## Tests

```bash
python3 -m pytest tests/
```

## Project layout

```
backend/
  app.py            Flask bootstrap, local frontend + /api blueprints
  config.py         env-driven config (dev / staging / production / testing)
  models.py         User, Court, CheckIn, Friendship, Message, Game, GamePlayer,
                    GameInvite, FavoriteCourt, Notification
  security.py       shared PostgreSQL / local-memory fixed-window rate limiter
  routes/           auth, courts (+ geocode), games, social (+ players/nearby), chat
  seed.py           court data importer (dir or bundled .json.gz) + demo seed
  wsgi.py           gunicorn entrypoint (backend.wsgi:app)
data/courts.json.gz bundled court dataset for first-boot seeding
public/             index.html, versioned CSS/JS, manifest, sw.js (no build step)
app.py              root Vercel Flask entrypoint
vercel.json         Flask framework and iad1 region configuration
tests/test_api.py   end-to-end API tests
scripts/migrate_sqlite_recovery.py
                    validated SQLite-to-Postgres recovery migration
```
