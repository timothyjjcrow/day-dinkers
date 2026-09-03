# Third Shot business integration guide

This document describes the business features that are executable in this
repository. It is deliberately specific about what is and is not connected.

## What works today

- A venue representative can claim a court, verify access to a business-domain
  mailbox, submit other evidence, receive reviewer feedback, and publish only
  after control and sensitive content have been approved.
- One organization can manage multiple locations. Roles are `owner`, `admin`,
  `editor`, and `viewer`; invitations are one-time, email-bound, and expiring.
- Businesses can publish descriptions, hours, offerings, dated or recurring
  schedule items, capacity, remaining spots, cancellations, lessons, events,
  memberships, and secure outbound booking links.
- The active `link_catalog` adapter accepts a structured Third Shot JSON
  catalog by authenticated owner push, a signed webhook, or a bounded public
  HTTPS pull. Syncs are versioned, idempotent, retried, and reconciled into a
  provider-neutral schedule.
- Link health checks, sync history, privacy-safe action analytics, conversion
  totals by currency, audit events, operator assignments, SLA states, reports,
  and two-person destructive approvals are durable database records.
- Provider secrets can be stored only as host-managed `env://` references or
  Fernet-encrypted `vault://` records. Public configuration, API responses,
  audit events, and logs never contain secret values or raw webhook bodies.

CourtReserve, Mindbody, Playbypoint, and PodPlay are shown as
`not_available`. Their names are not a claim of partnership, certification, or
working API access. A provider-specific adapter still requires that vendor's
contract, sandbox, credentials, mapping, and certification.

## Business and operator workflow

1. The representative claims the venue. The listing remains private.
2. The representative submits evidence. A business-email challenge confirms
   mailbox access. It counts as independently verified domain evidence only
   when the address matches an official venue domain; every claim still
   requires operator review.
3. A reviewer accepts or rejects evidence, records the verification method and
   feedback, then decides the claim. A competing ownership transfer requires a
   second, different administrator.
4. The verified representative inspects the draft and explicitly publishes it.
5. A later identity, contact, link, or logo change unpublishes the listing and
   enters the sensitive-content queue. Only an operator review makes it
   publishable again.
6. An owner or admin can create a structured feed. Editors may update its
   catalog; viewers may inspect status. A booking base must exactly match an
   already approved profile link, occurrence links must remain beneath that
   base, and imported data is public only while the business and connection
   pass all governance and current link-health gates.

Verification confirms control of a listing. It does not endorse a venue,
price, service, link, schedule, or availability claim.

## Role matrix

| Capability | Owner | Admin | Editor | Viewer |
|---|:---:|:---:|:---:|:---:|
| View private business operations | Yes | Yes | Yes | Yes |
| Edit profile, offerings, and schedule | Yes | Yes | Yes | No |
| Push an existing structured catalog | Yes | Yes | Yes | No |
| Publish, invite staff, create connections | Yes | Yes | No | No |
| Add locations, transfer, suspend, release | Yes | No | No | No |

Operator roles are separate from venue roles. `reviewer` can inspect queues and
perform ordinary reviewed actions; `admin` is required for credential writes
and the second approval of destructive actions. Security-sensitive operator
changes require a fresh TOTP code; read-only inspection and link rechecks do
not. Operator roles are granted only with the trusted direct-database script:

```bash
python3 scripts/manage_business_operators.py list
python3 scripts/manage_business_operators.py grant reviewer@example.com reviewer \
  --actor security-admin@example.com --reason "Business operations coverage"
```

## Structured catalog contract

The endpoint accepts one JSON object. Timestamps must include an offset or `Z`;
time zones must be IANA names such as `America/Los_Angeles`; URLs must use
HTTPS. The maximum public pull response is 1 MB.

```json
{
  "schema_version": 1,
  "source_version": "club-schedule-2026-09-01T15:00:00Z",
  "generated_at": "2026-09-01T15:00:00Z",
  "authoritative": true,
  "occurrences": [
    {
      "external_id": "open-play-2026-09-08",
      "title": "Intermediate open play",
      "kind": "open_play",
      "event_date": "2026-09-08",
      "start_time": "18:00",
      "end_time": "20:00",
      "timezone": "America/Los_Angeles",
      "capacity": 24,
      "spots_remaining": 7,
      "status": "scheduled",
      "skill_level": "intermediate",
      "location_note": "Courts 1–4",
      "instructor": "Venue staff",
      "price_text": "$12",
      "booking_url": "https://booking.example/open-play",
      "updated_at": "2026-09-01T14:55:00Z"
    }
  ],
  "conversions": [
    {
      "external_event_id": "paid-booking-1842",
      "occurrence_external_id": "open-play-2026-09-08",
      "occurred_at": "2026-09-01T15:05:00Z",
      "value_minor": 1200,
      "currency": "USD"
    }
  ]
}
```

`kind` supports `court_rental`, `lesson`, `open_play`, `clinic`, `league`,
`tournament`, `membership`, `event`, `hours`, and `other`. `status` supports
`scheduled`, `sold_out`, `cancelled`, and `completed`.

For one-time events, send `event_date` plus start/end times, or offset-aware
`starts_at` and `ends_at`. For a recurring definition, send `recurrence`,
`start_date`, `start_time`, and `end_time`; `end_date` is optional. An
authoritative snapshot marks previously imported occurrences absent from the
new snapshot as cancelled. Set `authoritative` to `false` for a partial update.

Conversion rows are optional and must use stable external event IDs. Third Shot
deduplicates them and reports values separately by currency; currencies are
never summed together.

## API sequence

All owner endpoints require the normal Third Shot bearer token.

```text
GET    /api/business-integrations/providers
POST   /api/businesses/:businessId/connections
GET    /api/businesses/:businessId/connections
PATCH  /api/businesses/:businessId/connections/:connectionId
PUT    /api/businesses/:businessId/connections/:connectionId/catalog
POST   /api/businesses/:businessId/connections/:connectionId/recheck
POST   /api/businesses/:businessId/connections/:connectionId/reconnect
DELETE /api/businesses/:businessId/connections/:connectionId
GET    /api/businesses/:businessId/integrated-schedule
GET    /api/businesses/:businessId/analytics?range=30d
```

Create a public-pull connection with this non-secret configuration:

```json
{
  "provider_key": "link_catalog",
  "display_name": "Club schedule feed",
  "config": {
    "label": "Live club schedule",
    "source_url": "https://club.example/third-shot/catalog.json",
    "booking_base_url": "https://booking.example"
  }
}
```

For direct pushes, send a stable `Idempotency-Key` header. A successful new
submission returns `202`; an exact retry returns the existing run without
duplicating schedule or conversion rows.

If occurrences include `booking_url`, the connection must declare
`booking_base_url`. That base must exactly equal the business profile's approved
website, booking, or membership URL, and every occurrence URL must stay on the
same HTTPS origin beneath the base path. A successful catalog parse proves data
validity, not link health. Run the connection recheck after creating or changing
the feed; public imported data remains held until every configured URL has a
current, exact-digest healthy check.

Public pulls are conditional (`ETag` and `Last-Modified`), limited in size and
redirect count, and protected against SSRF by validating every DNS result and
connecting to the validated public IP while preserving TLS SNI and hostname
verification. Private IPs, non-HTTPS URLs, credentials in URLs, non-JSON
responses, oversized bodies, and unsafe redirects fail closed.

## Signed webhooks

The webhook URL is:

```text
POST /api/business-integrations/webhooks/link_catalog/:connectionPublicId
```

Send the structured catalog as the raw request body. Include a stable
`X-Provider-Event-Id` and an `X-Third-Shot-Signature` header:

```text
t=<unix-seconds>,v1=<hex HMAC-SHA256(secret, "<unix-seconds>.<raw-body>")>
```

The timestamp tolerance is five minutes. Third Shot stores only payload and
signature digests plus normalized, validated catalog facts; it does not retain
the raw webhook body. Event ID and content-derived idempotency prevent replayed
deliveries from creating duplicate data.

Webhook secrets are provisioned by an administrator over HTTPS with current
MFA and are returned only as the boolean `webhook_configured`:

```text
POST /api/operator/business/connections/:connectionId/credentials
```

The request accepts `purpose` (`credential`, `webhook`, or `cursor`), `secret`,
and `mfa_code`. Secret values must never be placed in a connection's public
configuration, integration request, log, screenshot, or support message.

## Scheduled operations and environment

Vercel calls `GET /api/cron/business-integrations` with
`Authorization: Bearer $CRON_SECRET`. Each run has a hard item cap, uses row
locks/`SKIP LOCKED` where available, and isolates failures so one bad feed does
not stop unrelated pulls, retries, or link checks. Suspended, relinquished, or
unverified businesses are excluded from retries, pulls, and connection checks.
Work is claimed one item at a time in a round-robin across connection health,
profile health, retries, and catalog pulls. Each item commits independently,
and the route stops claiming work before the serverless function deadline.
Healthy evidence remains publishable for a bounded grace window so the daily
schedule does not make booking inventory disappear between routine checks;
an unhealthy result still takes effect immediately.

Production requires these independent secrets and settings:

```text
MFA_ENCRYPTION_KEY=<Fernet key>
MFA_ISSUER=Third Shot
PUBLIC_APP_URL=https://third-shot.vercel.app
RESEND_API_KEY=<Resend server key>
TRANSACTIONAL_EMAIL_FROM=Third Shot <verified-sender@example.com>
CRON_SECRET=<independent random secret>
BUSINESS_INTEGRATION_CRON_LIMIT=40
BUSINESS_INTEGRATION_CRON_TIME_BUDGET_SECONDS=45
BUSINESS_LINK_HEALTH_RECHECK_HOURS=20
BUSINESS_LINK_HEALTH_VALID_HOURS=72
BUSINESS_CATALOG_PULL_INTERVAL_MINUTES=1200
BUSINESS_CREDENTIAL_VAULT=encrypted_sql
BUSINESS_CREDENTIAL_ENCRYPTION_KEY=<different Fernet key>
BUSINESS_CREDENTIAL_KEY_VERSION=1
```

The cron response reports `processed`, queue-specific claimed/completed counts,
`time_budget_exhausted`, and the reserved shutdown headroom. Alert on repeated
exhaustion or failures rather than treating a single partial run as data loss;
unclaimed work remains due for the next invocation. The bundled Vercel Hobby
schedule runs daily, so the default makes pulls due four hours before the next
run to absorb scheduler jitter. A shorter pull interval requires a more
frequent external authenticated scheduler or durable worker; changing the
interval alone does not increase refresh frequency.

Generate the two Fernet keys separately:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Old credential keys may be retained temporarily as
`BUSINESS_CREDENTIAL_ENCRYPTION_KEY_V<n>` during a controlled rotation. Do not
reuse the JWT signing key, MFA key, credential key, cron secret, or webhook
secret for another purpose.

## Migration and release order

Production serverless instances do not run DDL. Before deploying code, run the
trusted migration against the direct, unpooled database URL:

```bash
read -rs TARGET_DATABASE_URL
export TARGET_DATABASE_URL
python3 scripts/migrate_production_schema.py
python3 scripts/migrate_production_schema.py --check-only
unset TARGET_DATABASE_URL
```

Then configure the environment, deploy, and verify the complete story: claim,
email challenge, evidence review, claim approval, explicit publish, team invite,
catalog sync, public schedule, action analytics, sensitive edit review, report,
and two-person control action. A successful build or health response alone does
not prove those paths work.

## Adding a proprietary provider

A provider is not marked active until all of the following exist:

1. Written vendor API access and a sandbox account.
2. A registered `ProviderAdapter` with an explicit auth mode and capabilities.
3. OAuth or credential-vault setup with rotation and revocation behavior.
4. Mapping fixtures for schedules, inventory, instructors, booking URLs,
   cancellations, pagination, time zones, and conversions.
5. Signed-webhook verification or bounded polling with retry/idempotency rules.
6. Contract, reconciliation, privacy, failure, and end-to-end browser tests.
7. Monitoring, support ownership, rollback instructions, and vendor approval.

Until those checks pass, the provider must remain `not_available`; a completed
support request means the request was handled, not that a connector was
installed or data is syncing.
