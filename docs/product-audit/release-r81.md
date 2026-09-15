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

Outstanding release gates: explicit approval for a fresh private production backup, then production migration/deployment and live verification. Production remains r79 until those steps are complete.

Automatic approval review previously rejected exporting the production database to a local backup because generic deployment authorization did not explicitly cover potentially sensitive user records and messages at that destination. Do not retry or bypass that export without specific user approval. The prepared r81 backup destination is `/Users/timothycrowley/pickleball local /tmp/product-audit-r81/production-before-r81.dump`; it will be excluded from Git and deployment uploads and restricted to the local user. This backup has not been created.

No matching recurring automation exists. Available goal tools cannot pause a goal, and computer use cannot control Codex. Do not mark the unfinished app goal complete or blocked merely to pause it. Stop implementation here and use the app's user-operated Pause control for the active goal.
