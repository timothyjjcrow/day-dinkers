# Third Shot

A social app for pickleball players, built to work across the US: find courts on a
map, discover players near you, check in, add friends, chat, schedule casual or
ranked (and recurring) games, and climb the rankings.

## Stack

- **Backend** — Flask + SQLAlchemy (SQLite for local dev, Postgres in production),
  JWT auth. All endpoints under `/api`.
- **Frontend** — single-page mobile-first web app in `public/` (vanilla JS +
  Leaflet), served by Vercel's CDN and by Flask locally. Production browser
  assets are emitted with `npm run build:frontend` as minified, versioned,
  Brotli/Gzip-compressed files; the readable sources remain the development
  source of truth.
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

- **Courts & map** — clustered map of ~18.5k US courts with live activity,
  amenity filters, text search, and **geocoding area search** ("jump to Austin,
  TX"). The default view leads with Search, **Active now**, **Filters**, and two
  recommended cards; exact court-name matches outrank similar place names.
  Selecting a result opens a state-aware **Now at this court** detail directly,
  suppressing empty metrics and counting only games the viewer can act on.
  Venue details, reviews, photos, and management actions stay behind progressive
  disclosures. Auto check-in by proximity is available while the app is open.
- **Business Hub** — authorized venue operators can claim one or more locations,
  verify a business-domain mailbox, complete a human control review, and publish
  only after sensitive content is approved. Organization-wide owner, admin,
  editor, and viewer roles support teams and multiple locations. Listings carry
  announcements, hours, amenities, contact details, booking and membership
  links, lessons, offerings, and dated or recurring schedules with capacity and
  availability. The active `link_catalog` connector accepts a versioned JSON
  feed by authenticated push, signed webhook, or a bounded HTTPS pull; imported
  schedules stay private until exact current link checks pass, and booking deep
  links must remain beneath an already reviewed profile URL. Link health, sync
  history, and privacy-safe action analytics are visible in the same dashboard.
  Integration requests and player reports enter
  an assigned operator queue with SLAs, MFA-gated reviews, immutable history,
  and two-person approval for destructive actions. Verified, published data is
  shown on the court page with clear paths to book, join, or learn more.
- **Location** — one optional, per-account first-run sheet sets a **home area**;
  the map and feeds open there. Community's People lane separates Friends and
  Nearby players, with compact filters and one contextual action per row.
- **Play** — two clear entry points separate **Find people to play** from **Start
  a ranked match**. Casual play is community-first: check in at a court, join
  players gathering in the next 5–15 minutes, share that you are free this hour,
  or plan an open-play session with a private group, friends, or nearby players.
  Ranked play stays match-focused with explicit singles/doubles format and
  options to find, challenge, or schedule a ranked match. At-court casual play
  safely joins or starts one live session in a single flow; nearby ready players
  see the underfilled group until it fills or expires. Remote players can choose
  **I can be there in 5–15 minutes** for a bounded hold on one spot, get
  Directions, and convert it only after a private exact-court check-in. Rally
  surfaces keep
  at-court, joined-roster, and arriving counts distinct; outsiders
  see only the aggregate while roster members see the traveler's ETA. Players
  who are not at a court can publish **Free this hour** at a selected
  court. The server fixes the signal to 60 minutes; a nearby player can accept
  it through expiry to atomically create one ordinary open casual game about
  15 minutes ahead, with retry-safe publish and acceptance keys and no false
  check-in presence. Planning is a true Where → When → Who flow with three
  smart time suggestions, a custom-time disclosure, compact answer summaries,
  and scheduling at any court,
  **recurring weekly open-play sessions**, join/leave, and an active-game banner.
  Join, waitlist, and instant-start changes confirm in place with Undo, Leave,
  or Cancel management. Every underfilled roster has one adaptive **Find
  players** sheet that leads with the best available channel—friends, court
  chat, or link sharing—and puts the rest under More. Public games can also
  publish a retry-safe live card in the court room. The card follows the real
  roster from open → full → open, offers join or waitlist without reserving a
  spot, and closes with the game instead of leaving stale recruiting text.
  Casual scores finalize instantly; ranked scores need an opposing player's
  one-tap confirmation (auto-confirm after 24h; disputes clear for re-entry)
  before ELO moves (K=32, team-average for doubles). Games still unscored after
  seven days move into durable Unscored history, notify their participants once,
  and accept a late result through the normal review flow for 30 days after
  play. Results feed, win streaks, and a podium leaderboard. **Game
  visibility**: open (anyone nearby) /
  friends / private (specific invitees). Challenges create private 1v1s.
- **Compete** — one **Create competition** path leads to equal Tournament and
  Box league choices. Full-page, keyboard-accessible tabbed details cover
  overview, matches/brackets, standings/players, and chat, with one global
  role-aware next action and one shared score → confirm → dispute → resolve
  result workflow.
  Exact match links reopen the right result (including older league rounds),
  while live refresh preserves scroll and form context.
- **Community** — stable **Messages / People / Groups** lanes keep DMs and active
  session chats focused, separate Friends from Nearby discovery, and give
  private play groups, public communities, court rooms, leagues, and tournaments
  a durable home. Chat is
  mobile-keyboard-aware
  across every room. Every text/photo send enters a durable, account-scoped
  outbox first, retries safely after offline/reload failures, and remains
  visibly retryable or removable without duplicate delivery.
- **Private play groups** — start a group directly or keep playing with people
  from a completed match. Durable invitations, a shared chat, a default court,
  and a one-tap session planner support up to 12 players. The accepted roster is
  server-owned and versioned, so a stale screen cannot invite a removed player
  or silently expose the group. Public community groups remain discoverable and
  can organize visible casual sessions for anyone who wants to join.
- **Me & Settings** — a compact dashboard leads with rating, next play, saved
  courts, and recent games; deeper stats and history stay under More. Settings
  is a separate five-destination hub for profile, notifications, privacy,
  appearance/calendar, and account controls.
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

### Applying additive schema upgrades to an existing deployment

Serverless production deliberately sets `SCHEMA_MANAGEMENT_ENABLED=false`, so
new tables and columns must be installed once through Neon's direct/unpooled
connection **before** deploying application code that uses them. The command is
idempotent, refuses pooled or unexpected databases, and verifies the required
release schema after applying the app's additive migrations. That includes the
Crew tables, instant-rally provenance, the one-active-check-in constraint, and
the arrival-intent table with its one-active-user and one-active-rally partial
unique indexes, plus the availability-pulse publish/accept retry ledger and its
one-active-pulse-per-user partial unique index. It also installs the durable
game-open-call ledger linking one host-authored recruiting card to one court
message, with active-game and retry uniqueness constraints. The same command is
the release-wide migration for the Business Hub: it installs and verifies its
governance, organization/team, evidence, revisions, reports, operator actions,
MFA columns, and all eight provider-sync foundation tables:

```bash
read -rs TARGET_DATABASE_URL
export TARGET_DATABASE_URL
python3 scripts/migrate_production_schema.py
# Later, verify without applying DDL:
# python3 scripts/migrate_production_schema.py --check-only
unset TARGET_DATABASE_URL
```

Run the final command from a trusted operator machine. Never place the direct
URL in a tracked file, command argument, chat message, or screenshot. Leave the
deployed `DATABASE_URL` on the pooled endpoint and keep runtime schema
management disabled.

Business reviews normally happen in the in-app operator queue. Operator roles
are separate from venue-team roles and may be provisioned only from a trusted
machine using the direct database URL. The target account must enable MFA before
it can receive a reviewer or administrator role:

```bash
read -rs TARGET_DATABASE_URL
export TARGET_DATABASE_URL
python3 scripts/manage_business_operators.py list
python3 scripts/manage_business_operators.py grant reviewer@example.com reviewer \
  --actor security-admin@example.com \
  --reason "Business operations coverage"
unset TARGET_DATABASE_URL
```

The operator desk covers evidence, claims, sensitive revisions, integration
requests, reports, connection health, assignment, and SLA state. All
security-sensitive operator changes require a current TOTP; recovery codes are
not accepted. Competing ownership transfers, forced suspensions, and
revocations require a different administrator to confirm the proposal. Approval
confirms control of a listing, not an endorsement, and leaves publication as a
separate venue decision. Direct-database review scripts remain available only
as audited break-glass tools.

`link_catalog` is the only executable connector today. CourtReserve, Mindbody,
Playbypoint, and PodPlay are displayed as unavailable, not as partnerships or
working API connections. A completed integration request means the request was
handled; it does not activate a connector. Never put API keys or passwords in a
request. The exact JSON/webhook contract, role matrix, vault model, cron
behavior, migration order, and provider-readiness checklist are documented in
[BUSINESS_INTEGRATION_GUIDE.md](BUSINESS_INTEGRATION_GUIDE.md); a machine-readable
schema and sample live in `schemas/` and `examples/`.

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
| `MFA_ENCRYPTION_KEY` | a Fernet key used only for encrypted TOTP seeds; required in production |
| `MFA_ISSUER` | authenticator-app issuer label, normally `Third Shot` |
| `RESEND_API_KEY` | server-side transactional-email key for verification and team invitations |
| `TRANSACTIONAL_EMAIL_FROM` | a verified Resend sender identity |
| `PUBLIC_APP_URL` | canonical HTTPS origin used in one-time email links |
| `CRON_SECRET` | independent high-entropy Bearer secret for every `/api/cron/*` route, including push, lifecycle maintenance, and business integrations |
| `BUSINESS_CREDENTIAL_VAULT` | `encrypted_sql` to enable encrypted provider-secret writes; defaults to disabled |
| `BUSINESS_CREDENTIAL_ENCRYPTION_KEY` | a separate Fernet key for provider secrets |
| `BUSINESS_CREDENTIAL_KEY_VERSION` | positive active provider-secret key version, initially `1` |
| `SERVERLESS_RUNTIME` | `true` |
| `SCHEMA_MANAGEMENT_ENABLED` | `false`; all DDL belongs in the one-time migration |
| `AUTO_CREATE_DB` | `false` |
| `AUTO_SEED_COURTS` | `false` for the recovered database |
| `RATE_LIMIT_BACKEND` | `database` |
| `PUSH_DELIVERY_ENABLED` | `false` during development; set `true` only when VAPID keys and an appropriate push schedule are ready |
| `VAPID_PUBLIC_KEY` | browser-safe public half of the web-push VAPID key pair |
| `VAPID_PRIVATE_KEY` | server-only private half of the web-push VAPID key pair |
| `VAPID_CLAIMS_EMAIL` | VAPID contact URI such as `mailto:ops@example.com` |

Optional integration bounds are `BUSINESS_INTEGRATION_CRON_LIMIT` (40–100),
`BUSINESS_INTEGRATION_CRON_TIME_BUDGET_SECONDS` (45–50 seconds),
`BUSINESS_LINK_HEALTH_RECHECK_HOURS` (6–24 hours),
`BUSINESS_LINK_HEALTH_VALID_HOURS` (48–168 hours), and
`BUSINESS_CATALOG_PULL_INTERVAL_MINUTES` (clamped to 15 minutes–24 hours).
The defaults recheck links before the next daily cron while keeping a verified
last-known-good destination available through a delayed or missed run. A newly
failed probe still removes the affected integration from publication
immediately. The source-deploy default makes pulls due after 20 hours so they
are ready before the next daily Vercel Hobby cron; use a more frequent
authenticated scheduler before promising a shorter refresh cadence.
Optional: `RATE_LIMIT_ENABLED` (default true). Never set `RESET_DB_ON_BOOT` on
the recovered database. The Hobby deployment schedules all three authenticated
jobs once per day: `/api/cron/maintenance` at 08:00 UTC for presence, score,
competition-result, recurrence, reminder, and digest lifecycle work;
`/api/cron/push` at 09:00 UTC for the durable phone-alert outbox; and
`/api/cron/business-integrations` at 10:00 UTC for the bounded provider
refresh. Vercel Hobby provides hour-level rather than exact-minute scheduling,
so these are target hours. All three require `Authorization: Bearer
$CRON_SECRET`. The push run is a safe no-op while phone delivery is disabled.
Before promising timely phone alerts, attach a frequent external scheduler or
upgrade Vercel and restore a minute-level push schedule; a daily push drain can
deliver time-sensitive alerts nearly 24 hours late.
Court presence uses a visible-client heartbeat and becomes stale after
`PRESENCE_STALE_AFTER_SECONDS` (default `1800`, or 30 minutes); the maintenance
job then checks out the stale row and closes any abandoned instant assembly.

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
                    GameInvite, GameOpenCall, FavoriteCourt, Notification
  security.py       shared PostgreSQL / local-memory fixed-window rate limiter
  integrations/     provider registry, catalog adapter, vault, sync, pull,
                    webhook, link-health, and reconciliation services
  routes/           auth, courts, games, social, chat, business + governance APIs
  services/         business governance, MFA, push, and shared service logic
  seed.py           court data importer (dir or bundled .json.gz) + demo seed
  wsgi.py           gunicorn entrypoint (backend.wsgi:app)
data/courts.json.gz bundled court dataset for first-boot seeding
public/             index.html, source CSS/JS, generated release assets, manifest, sw.js
app.py              root Vercel Flask entrypoint
vercel.json         Flask framework and iad1 region configuration
tests/test_api.py   end-to-end API tests
scripts/migrate_sqlite_recovery.py
                    validated SQLite-to-Postgres recovery migration
scripts/migrate_production_schema.py
                    one-command additive release migration and verifier
schemas/             machine-readable business catalog contract
examples/            example business catalog payload
```
