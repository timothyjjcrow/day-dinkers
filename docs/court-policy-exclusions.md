# Operator pickleball-prohibition exclusions

An explicit current venue policy can prohibit pickleball while the facility
remains open for tennis. The `pickleball_prohibited` directory reason preserves
the original Court ID, metadata, and history and suppresses only discovery. A
direct detail link explains the policy without exposing internal review data.

Admission requires an unqualified prohibition statement, exact citations to
operator/government source facts, a citation hosted by the Court's operator
website, and the complete current sixteen-field Court identity. Absence from a
court inventory, private membership, temporary restrictions, and court-count
discrepancies do not qualify. The normal immutable publication intent, stale
identity checks, readback, and reversible exclusion rollback remain in force.

The source-qualified two-record dossier is saved in
`data/court-enrichment/publishing/flag-audit/court-policy-exclusions-source-qualified-research-20261003.json`.
Waterstone 6331 was freshly fetched from its HOA's current amenities page.
Faulkner 16089's direct primary fetch returned 403; current primary search text
and the parent’s SHA-bound official HTML independently show the explicit ban.
Both owner-reviewed plan/apply/API verification receipts and freeze artifacts
are hash-linked. Fresh public details matched the database identity guards; all
22 dependency groups per Court are zero. Counts and existing fees/hours remain
unchanged by the proposal.

The read-only production schema proof is
`data/court-enrichment/publishing/court-policy-exclusion-schema-dry-20261003T195919Z.json`.
It recognized the existing validated two-reason check and proposed precisely
two statements, in one transaction, replacing only
`picklepals.court_directory_exclusion.ck_court_directory_exclusion_reason`.
The migration shares the exclusion publisher advisory lock, limits lock waits,
hashes existing exclusion rows before/after, and rolls back on validation or
readback failure. Repeating an applied migration performs no DDL. It never
creates a missing table, changes a key/column, or touches Court/history rows.

The operator helper defaults to a read-only dry run and accepts only an explicit
direct `TARGET_DATABASE_URL`. In an approved environment, use fresh immutable
output paths:

```sh
python -m scripts.migrate_court_directory_exclusion_policy --output schema-dry.json
python -m scripts.migrate_court_directory_exclusion_policy --apply --output schema-applied.json
```

The `--apply` invocation writes and flushes a separate immutable intent before
DDL. The root publisher owns this migration, compatible deployment, fresh
publication plan/dry run, activation, and public verification. This branch has
performed no production DDL, record activation, or deployment.

The existing `.vercelignore` allowlist excludes this standalone migration,
publisher, tests, docs, and research. Neither build nor app startup imports or
executes the helper. The existing table-creation migration is unchanged.

Validation: 131 focused tests passed, including source/identity/scope rejection,
canonical/alias discovery, original ID/history preservation, schema contracts,
operator migration rollback/idempotency, runtime-import isolation, and Vercel
build tests. PostgreSQL DDL transaction tests use a controlled fake connection;
the actual production check was inspected through a read-only dry run.
