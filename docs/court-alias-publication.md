Court aliases represent reviewed duplicate venue identities. They preserve every
Court row, ID, foreign key and existing link. Active aliases disappear from the
shared directory query before filters, sorting, counts and pagination. Literal
and the existing conservative fuzzy search can still find the canonical venue
through an alias's name, address or city. Canonical facts determine location,
amenities, hours and court count; counts are never added together.

Canonical discovery, detail and play readers include active-family presence and
games using the existing freshness and privacy rules. Presence counts distinct
players. Canonical ratings and review readers use each player's latest family
review, ordered by updated_at then ID. Canonical review edits preserve the
selected review's original court ID. A player's explicit canonical review DELETE
removes that player's family feedback, preventing an older copy resurfacing.
Direct alias readers and actions retain their original court scope. Activation
never edits, deletes or moves history. Photos, venue business data, chat,
favorites, check-in history and leaderboard history remain scoped to their
original IDs.

The initial three mappings are 12628 → 1356, 3724 → 3725 and 3894 → 4316. Their
reviewed evidence is in
`data/court-enrichment/publishing/court-aliases-initial-research.json`. The research
file is a proposal, not an executable snapshot of production. Planning captures
fresh court identity guards, exact alias before/after values and dependency
counts. Production activation requires zero dependent rows on both source and
canonical court. Future activation with history needs a broader preservation
review even though this release safely reads new activity created after an alias
is activated.

Operator sequence (run from the reviewed release checkout, with explicit
TARGET_DATABASE_URL already set to its direct/unpooled PostgreSQL URL):

```sh
python3 scripts/migrate_production_schema.py
python3 scripts/migrate_production_schema.py --check-only
python3 scripts/publish_court_aliases.py plan \
  --research data/court-enrichment/publishing/court-aliases-initial-research.json \
  --reviewed-by live-publisher \
  --output data/court-enrichment/publishing/court-aliases-initial-plan.json
python3 scripts/publish_court_aliases.py apply \
  --plan data/court-enrichment/publishing/court-aliases-initial-plan.json \
  --report data/court-enrichment/publishing/court-aliases-initial-dry-run.json
```

Review the fresh plan and dry-run report. Stage the application against the exact
current live Git revision, preserve that revision's deployment configuration and
build migration hooks, run its tests, and verify the deployment through the
existing protected-preview process. Activation is explicit:

```sh
python3 scripts/publish_court_aliases.py apply \
  --plan data/court-enrichment/publishing/court-aliases-initial-plan.json \
  --report data/court-enrichment/publishing/court-aliases-initial-applied.json \
  --apply
```

The importer never imports backend.app, loads dotenv, or creates schema. It
discovers every live Court foreign key, checks both sides, and also checks
polymorphic court Conversation scopes. PostgreSQL takes a transaction advisory
lock plus sorted Court FOR UPDATE locks: concurrent importer calls cannot form a
chain/cycle, and concurrent Court FK inserts cannot pass the dependency check
before commit. Both identity and exact mapping guards are rechecked inside this
transaction. All mappings commit together. Use this importer for additions;
arbitrary direct SQL bypasses the graph and activation guards.

Outputs are exclusive files. Before the first write, an immutable
`REPORT.intent.json` contains the plan hash, original/proposed values, current
court identities and current dependency counts. `REPORT` records the committed
after values. An intent without a completed report requires reconciliation before
retrying; never overwrite either artifact. Repeated application of the same plan
is idempotent, using a new report path. Failed/stale guards abort the whole batch.

Audited rollback defaults to a dry run as well:

```sh
python3 scripts/publish_court_aliases.py apply \
  --plan data/court-enrichment/publishing/court-aliases-initial-plan.json \
  --report data/court-enrichment/publishing/court-aliases-initial-rollback-dry-run.json \
  --rollback
python3 scripts/publish_court_aliases.py apply \
  --plan data/court-enrichment/publishing/court-aliases-initial-plan.json \
  --report data/court-enrichment/publishing/court-aliases-initial-rollback.json \
  --rollback --apply
```

Rollback disables newly inserted alias rows and restores an existing row's exact
prior values. It does not remove Court rows or player data. Reactivation after
rollback requires a newly reviewed fresh plan. No runtime worker cache stores
alias mappings, so reviewed DB-only additions or disables take effect on the next
request after this release is deployed. Production/serverless startup continues
to forbid schema mutation; operator/build migrations create the additive table.

Focused verification:

```sh
APP_ENV=testing python3 -m pytest -q \
  tests/test_court_aliases.py tests/test_court_alias_publication.py \
  tests/test_production_schema_migration.py tests/test_court_map_quality.py \
  tests/test_court_activity_confidence.py tests/test_court_review_reader_api.py \
  tests/test_court_play_timeline.py tests/test_court_presence_and_review.py \
  tests/test_court_planning_times.py tests/test_serverless.py
```

After activation/release, verify the directory loses exactly three venues;
canonical names and court counts remain unchanged; each alias-only name finds
one canonical result; all six original detail URLs still return their original
IDs; and current schema check-only passes. Do not create production player
activity just to test this rollout; preservation/aggregation paths are covered by
isolated fixtures.
