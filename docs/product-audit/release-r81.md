# r81 release checkpoint

User direction, September 14–15, 2026: finish the current improvement, publish the accumulated changes, verify them, then stop the whole-app improvement loop. Do not begin another UI pass.

This candidate packages the accumulated UI work through nearby live play (`0b915ba`), including reviewed session entry, dated host handoffs and cancellations, court planning and venue information. The shell and service worker now load r81 immutable files, while r79/r80 remain available to existing tabs. The court planning-time endpoint now has a 60-request/minute limit.

Release preparation verified locally:

- Frontend build completed. Application Brotli transfer is 305,061 bytes (297.9 KiB); aggregate runtime transfer is 380,930 bytes (372.0 KiB). The aggregate budget is explicitly increased from 370 to 375 KiB for the additional UI work; the application cap remains 300 KiB. This is additional functionality, not a claimed compression improvement.
- 80 focused asset, shell, invitation and rate-limit checks passed. An earlier full run had 1,870 passes and eight failures; seven were outdated release/join fixture expectations and one exposed the missing endpoint rate limit. Those failures have been corrected; the subsequent complete CI run passed.
- All seven real PostgreSQL concurrency cases passed, including first join and waitlist acceptance racing against a price edit. Both wait on a real database lock, reject stale terms without taking a place, and succeed after reviewing the updated price.
- An additional 32 session-entry, recurring-host review and following-date edit API tests passed against a separate disposable UTF-8 PostgreSQL database.
- Rehearsed the migration twice on a new clone of the synthetic r79 database. Existing user, court, game, roster and message rows were unchanged; the new roster confirmation column remained null for legacy membership. No production records were accessed or exported.

Packaged browser verification on the isolated in-memory fixture at port 8181 passed: login, city search/selection, nearby-play empty-state refresh, session detail, overlapping-time review, accepted join (confirmed roster increased from one to two), correct court detail, and the venue workspace. All four scripts loaded from `/release-assets/r81/`; browser errors were empty. Screenshots at 390px dark and 320px light were visually inspected. Page and modal scroll widths matched the viewport. Evidence is in the primary workspace’s ignored `output/product-audit/evidence/release-r81/` directory. Geolocation permission was unavailable in this browser; city search provided the working alternative. Physical-device checks remain outside this release verification.

Exact-code CI passed for `c591da174e31406b77fde3d1d8633c6a8bc97098`: [Backend CI 34965279940](https://github.com/timothyjjcrow/day-dinkers/actions/runs/34965279940), 1,883 passed, one opt-in PostgreSQL module skipped, one warning. The separately run PostgreSQL checks passed as recorded above. CI also verified the generated files reproduce exactly on Linux and built the backend container successfully. Subsequent checkpoint edits affect documentation only.

## Production deployed

On September 15, the user again explicitly requested deployment. The earlier local database export was not retried. A narrower migration resolved the deployment hold: a read-only production metadata audit found exactly one missing nullable column, `game_player.commitment_requested_at`. The exact single-column migration was rehearsed twice against a fresh synthetic r79 clone, then applied in a transaction with a five-second lock timeout and fifteen-second statement timeout:

```sql
ALTER TABLE picklepals.game_player
ADD COLUMN IF NOT EXISTS commitment_requested_at TIMESTAMP WITHOUT TIME ZONE;
```

The column type, nullability and absence of a default were checked before committing. No production records were read, exported, updated or deleted by the migration. A subsequent complete read-only schema audit returned no gaps. The previous application remains compatible with the added column, so application rollback does not require dropping it. This approach supersedes the earlier assumption that deploying required a local production dump; the rejected export remains unauthorized and was never performed.

- Release source: `20e9414b8b65e66974544bf717fab54020d086a9`, pushed to `main`. It differs from CI-tested `c591da1` only in checkpoint documentation.
- Production deployment: `dpl_Ep3Q7Xoys4uaTmzCTzg6xtpHgK7C`, [immutable deployment](https://third-shot-hiyn1ejeg-timothyjjcrows-projects.vercel.app).
- Live site: [third-shot.vercel.app](https://third-shot.vercel.app/).
- Previous deployment retained for rollback: `dpl_2dVrHgRNQxfvxHHTYnwntUTSWnjg`, `third-shot-onh1w3v31-timothyjjcrows-projects.vercel.app`.

The deployment was built with production configuration without assigning domains. Candidate health, public court discovery, and all five runtime files passed before promotion. After promotion, the public domain passed the same byte comparisons and compression/immutable-header checks; `/health` returned production with `db: true`; service-worker cache r83 referenced r81; the prior r79 app file remained available unchanged for existing tabs. A fresh mobile browser searched Portland and opened Portland Tennis Center correctly, with no JavaScript errors. The deployment error-log query returned no error entries during the verification window. Live checks were read-only; authenticated mutations were verified in the isolated fixtures and automated suites above.

Live screenshot: primary workspace `output/product-audit/evidence/release-r81/production-court390.png`. No production database backup was created. Temporary Vercel environment values were passed in process memory, not saved into repository files.

## Stop checkpoint

The requested accumulated improvements are deployed. Do not begin another UI pass until the user resumes. The whole-app goal remains unfinished; this release is not a completion claim for that larger review. The goal was previously marked blocked after the repeated backup approval impasse. Available goal tools cannot change it to paused; do not mark it complete to simulate a pause. Preserve the user's requested stop.
