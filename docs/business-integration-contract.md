# Business integration contract

This document separates executable capabilities from planned vendor work. The
application must not describe an unavailable vendor as connected, partnered,
or supported.

## What is active

`link_catalog` is the only active provider adapter. A verified business can:

- push the versioned JSON contract through its authenticated manager account;
- expose the same JSON at an HTTPS `source_url` for conditional scheduled pulls;
- have an operator attach a one-time webhook secret, then push signed webhook
  deliveries; and
- publish normalized schedule, lesson, event, inventory, booking-link, and
  conversion records.

Imported records are held from the public API until the connection is connected
and every configured URL has a current healthy check. An occurrence booking URL
also requires a `booking_base_url` that exactly matches an operator-approved
business-profile link and must remain on its HTTPS origin beneath that base
path. Parsing a catalog never marks links healthy.

CourtReserve, Mindbody, Playbypoint, and PodPlay are explicit
`not_available` descriptors. No credentials, vendor API client, OAuth exchange,
partnership, or certification exists for those providers today.

## Runtime configuration

Production requires:

- `CRON_SECRET`: high-entropy value used as the exact Bearer token for
  `GET /api/cron/business-integrations`.
- `BUSINESS_CREDENTIAL_VAULT=hybrid` (or `encrypted_sql`) to enable SQL-backed
  credential writes. The default is `disabled` and fails closed.
- `BUSINESS_CREDENTIAL_ENCRYPTION_KEY`: a Fernet key used to encrypt new secret
  rows. Secret material is never stored in plaintext.
- `BUSINESS_CREDENTIAL_KEY_VERSION`: positive integer, initially `1`.
- `BUSINESS_CREDENTIAL_ENCRYPTION_KEY_V<n>`: old key versions retained only
  while ciphertext rows are rotated to the current version.
- `MFA_ENCRYPTION_KEY`: existing MFA Fernet key; operator secret writes,
  credential-reference changes, disconnects, and reconnects require a fresh
  TOTP. Recovery codes are not accepted for these actions.

Optional bounded settings are `BUSINESS_INTEGRATION_CRON_LIMIT` (40–100 per
invocation), `BUSINESS_INTEGRATION_CRON_TIME_BUDGET_SECONDS` (45–50
seconds), `BUSINESS_LINK_HEALTH_RECHECK_HOURS` (6–24 hours),
`BUSINESS_LINK_HEALTH_VALID_HOURS` (48–168 hours), and
`BUSINESS_CATALOG_PULL_INTERVAL_MINUTES` (clamped to 15 minutes through 24
hours). The default 20-hour recheck and 72-hour last-known-good validity window
fit the daily production cron. A completed unhealthy check invalidates
publication immediately; the grace window only protects against delayed or
missed checks. The default catalog pull interval is 20 hours so work is due
before the next daily cron despite scheduler jitter; a shorter value requires
a scheduler that actually invokes the endpoint more frequently.

Host-provisioned `env://BUSINESS_PROVIDER_SECRET_*` references remain readable
in `hybrid` mode. They are read-only. Writable secrets use random
`vault://<uuid>` references; SQL stores only Fernet ciphertext, purpose, key
version, lifecycle timestamps, and creator ID. Secret APIs never return a
reference or plaintext. Deletion erases ciphertext and keeps a tombstone so an
old reference cannot be reused.

## JSON feed schema

The response must be an `application/json` object no larger than 1 MB:

```json
{
  "schema_version": 1,
  "source_version": "venue-catalog-42",
  "generated_at": "2026-09-01T15:00:00Z",
  "authoritative": true,
  "occurrences": [
    {
      "external_id": "clinic-2026-09-08",
      "title": "Intermediate clinic",
      "kind": "clinic",
      "event_date": "2026-09-08",
      "start_time": "18:00",
      "end_time": "20:00",
      "timezone": "America/Chicago",
      "capacity": 24,
      "spots_remaining": 7,
      "location_note": "Courts 1–4",
      "instructor": "Venue staff",
      "booking_url": "https://venue.example/book/clinic-2026-09-08",
      "updated_at": "2026-09-01T14:55:00Z"
    }
  ],
  "conversions": [
    {
      "external_event_id": "paid-booking-1001",
      "occurrence_external_id": "clinic-2026-09-08",
      "occurred_at": "2026-09-01T15:05:00Z",
      "value_minor": 2500,
      "currency": "USD"
    }
  ]
}
```

Recurring occurrences use `recurrence`, `start_date`, optional `end_date`,
`start_time`, `end_time`, and an IANA `timezone`. One-off occurrences may use
those dated fields or timezone-aware `starts_at`/`ends_at`. Collections are
limited to 500 occurrences and 500 conversions per snapshot. Every persisted
external link must be HTTPS.

Scheduled pulls resolve and reject all non-public targets, connect to one of the
validated IPs while preserving the original host for TLS SNI/certificate checks,
and repeat validation for every redirect. They send conditional ETag and
Last-Modified headers when available. They never send a credential.

## Operations and privacy

The cron route round-robins retry runs, due catalog pulls, stale provider link
checks, and stale verified-profile website/booking/membership checks with row
locking, one-item commits, a shared hard work cap, and a serverless time budget.
Its response and structured logs contain only
aggregate counts, safe error codes, request ID, duration, and internal
connection/run IDs. URLs are represented only by SHA-256 digests in health
records and are never logged. Suspended, relinquished, and unverified businesses
are excluded from background integration work.

Click analytics accepts `profile_view`, `website`, `contact`, `schedule`,
`booking`, `lesson`, `membership`, `event`, and `open_play`. It stores no user
ID, IP address, user agent, or destination URL. Conversion values are aggregated
per currency; values from different currencies are never summed together.

Run the additive production migration before deploying these routes:

```bash
python3 scripts/migrate_production_schema.py
python3 scripts/migrate_production_schema.py --check-only
```

The release-wide migration creates and verifies both governance and provider
foundation schema. The standalone integration migration remains a focused
development diagnostic; it is not the production release command.
