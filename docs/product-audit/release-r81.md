# r81 release checkpoint

User direction, September 14–15, 2026: finish the current improvement, publish the accumulated changes, verify them, then stop the whole-app improvement loop. Do not begin another UI pass.

This candidate packages the accumulated UI work through nearby live play (`0b915ba`), including reviewed session entry, dated host handoffs and cancellations, court planning and venue information. The shell and service worker now load r81 immutable files, while r79/r80 remain available to existing tabs. The court planning-time endpoint now has a 60-request/minute limit.

Release preparation verified locally:

- Frontend build completed. Application Brotli transfer is 305,061 bytes (297.9 KiB); aggregate runtime transfer is 380,930 bytes (372.0 KiB). The aggregate budget is explicitly increased from 370 to 375 KiB for the additional UI work; the application cap remains 300 KiB. This is additional functionality, not a claimed compression improvement.
- 80 focused asset, shell, invitation and rate-limit checks passed. An earlier full run had 1,870 passes and eight failures; seven were outdated release/join fixture expectations and one exposed the missing endpoint rate limit. Those failures have been corrected; a fresh complete CI run is required.
- All seven real PostgreSQL concurrency cases passed, including first join and waitlist acceptance racing against a price edit. Both wait on a real database lock, reject stale terms without taking a place, and succeed after reviewing the updated price.
- Rehearsed the migration twice on a new clone of the synthetic r79 database. Existing user, court, game, roster and message rows were unchanged; the new roster confirmation column remained null for legacy membership. No production records were accessed or exported.

Outstanding release gates: packaged browser smoke, exact-commit Linux CI, explicit approval for a fresh private production backup, then production migration/deployment and live verification. Production remains r79 until those steps are complete.

Automatic approval review previously rejected exporting the production database to a local backup because generic deployment authorization did not explicitly cover potentially sensitive user records and messages at that destination. Do not retry or bypass that export without specific user approval.

No matching recurring automation exists. Available goal tools cannot pause a goal, and computer use cannot control Codex. Do not mark the unfinished app goal complete or blocked merely to pause it. Stop implementation here and use the app's user-operated Pause control for the active goal.
