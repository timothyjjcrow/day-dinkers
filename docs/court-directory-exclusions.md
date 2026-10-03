# Reviewed court directory exclusions

`CourtDirectoryExclusion` controls discovery visibility without changing a Court
row or any game, review, favorite, check-in, chat or business reference. Active
rows are filtered before geographic and amenity filters, literal/fuzzy search,
ranking, totals and cursor pagination. An excluded alias name cannot restore a
canonical search result. Original detail and history routes keep the original ID;
detail responses include a plain `directory_context` explanation.

Only two reasons are supported:

- `foreign_venue`: a reviewed physical location outside the US and its territories,
  with explicit country, locality, address and primary-source location citations.
- `invalid_test_record`: an explicit source-publisher admission that a listing is
  a nonexistent test fixture. Unknown capacity, a zero count, seasonal availability,
  programs, generic non-court concerns and permanent closure are not sufficient.

The table has a restrictive Court foreign key, an active flag, a reason-code check,
concrete review reason, public source URLs, reviewer and UTC review timestamps.
There is no public write API, startup DDL or automatic exclusion rule.

## Release prerequisite

Use the existing additive release migration and verifier from the reviewed commit
before promoting the new backend. The old backend continues to serve while the
new table is created. Configure the direct, unpooled `TARGET_DATABASE_URL` through
the operator's secret store; the commands never load a dotenv file or print it.

```bash
python3 scripts/migrate_production_schema.py
python3 scripts/migrate_production_schema.py --check-only
```

Keep production `SCHEMA_MANAGEMENT_ENABLED=false` and `AUTO_CREATE_DB=false`.
The existing migration pipeline, frontend source/assets and deployment base are
preserved. No exclusions are activated by creating the schema or deploying code.

## Reviewed publication

Source research is a JSON object containing 1–100 `records`. Each record requires
`court_id`, `reason_code`, a concrete `reason`, and `sources` with public HTTP(S)
URLs, titles, ISO access dates and a nonempty list of reviewed facts. A foreign
record additionally requires `foreign_location` (`country_code`, `country`,
`locality`, `address`) and `location_source_urls` citing at least one source whose
`authority` is `operator`, `government` or `sports_governing_body`.

A test fixture requires `source_publisher_admission` with exactly `kind`, `text`
and `source_urls`; `kind` must be `nonexistent_test_fixture`. Its admission URLs
must cite reviewed sources with `authority=source_publisher`. A human reviewer
must confirm the actual source admission; source labels are not automated proof.

```bash
python3 scripts/publish_court_directory_exclusions.py plan \
  --research reviewed-research.json --reviewed-by "Named reviewer" --output plan.json
python3 scripts/publish_court_directory_exclusions.py apply \
  --plan plan.json --report dry-run.json
python3 scripts/publish_court_directory_exclusions.py apply \
  --plan plan.json --report applied.json --apply
python3 scripts/publish_court_directory_exclusions.py apply \
  --plan plan.json --report rollback-dry-run.json --rollback
python3 scripts/publish_court_directory_exclusions.py apply \
  --plan plan.json --report rollback.json --rollback --apply
```

Every output path must be new. Planning and execution without `--apply` use a
read-only transaction and perform no DDL. Apply takes an independent advisory
lock and sorted Court/exclusion row locks, checks exact Court identities and
exclusion before-values, flushes an immutable `.intent.json` audit before the first
write, and verifies transaction readback. Reports bind the plan content hash.
Actual foreign-key and court-conversation dependency counts are audited; they do
not block visibility changes because no dependency is moved or deleted.

Rollback restores the exact prior exclusion row, or retains a disabled additive
row when none existed. Later exclusion changes cause a stale-guard rejection.
Rollback can restore visibility after independent Court metadata corrections;
it still checks the exact exclusion configuration. Reactivation after rollback
requires a fresh reviewed plan. Original Court and history rows stay intact.
