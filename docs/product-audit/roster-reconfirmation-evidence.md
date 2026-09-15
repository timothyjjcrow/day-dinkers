# Roster and changed-plan review — September 14

Status: in progress on `codex/roster-commitment-resume`, restored at `/tmp/thirdshot-roster-resume` from the pushed WIP branch after its old temporary worktree disappeared. Production remains r79. The previous turn made progress by saving and pushing the paused work; this resumed pass implements and verifies further changes.

## Behavior and UI

Material plan edits keep guests’ places while requesting explicit confirmation. Per-player groups distinguish Confirmed, Confirm again and Place reserved. Existing join retries and stale confirmation requests cannot silently renew consent. Accepting a host handoff now requires confirmation of any changed plan on the affected dates before any hosting/roster mutation occurs; the UI opens the relevant date for review.

The confirmation action is full width, with a quieter decline action. A held place uses a clock instead of a confirmation checkmark. The roster follows the plan directly; optional calendar/installation controls follow the session actions. Status headings, readable available-space/count text, and wrapping names make the roster easier to scan. Dialog scroll padding follows the actual sticky title height, including refreshed headers, so enlarged-text focus targets remain visible.

The user explicitly requested UI improvement on every pass. Apply `ui-pass-standard.md` to all remaining audit areas.

## Verified this pass

- Resumed API and roster tests: 12 passed before new changes.
- Handoff/reconfirmation, consent, dated interactions and roster tests: 28 passed.
- Relevant modal and session UI checks: 71 passed.
- Frontend sweep: 742 passed, one old exact-icon assertion failed after the intentional pending-clock change. That assertion was updated and passed in the subsequent release-focused checks.
- Sticky-header resize/replacement/cleanup regression: passed.
- Additive legacy-roster migration is exercised twice, preserving attendance timestamps and leaving the new request marker NULL; it passed in the release-focused run.
- Synthetic browser: Alex opened Sunday rotations at 390px, saw the $10 price and court booking requirement, confirmed through the UI, and moved to Confirmed 2. A separate host API read showed the same persisted count.
- Stale browser action: loaded a $12 plan, changed it to $15 through the host API, then clicked the old confirmation. The UI displayed $15, focused the confirmation title and explained the change. A separate host read still showed one confirmed and one needing confirmation.
- At 320px, light/dark mode and manually doubled computed text sizes, the dialog’s content width stayed 320px. The measured enlarged header bottom was 258.8px and confirmation panel top 267.6px after the inset fix. Keyboard Tab reached the confirmation button below the sticky title; Enter completed confirmation. No browser script errors were reported.
- Screenshots are in the primary workspace’s ignored `output/product-audit/evidence/roster-r80/`. They use synthetic accounts and sessions.

## Release and remaining scope

The r80 bundle is prepared with a new r82 service-worker cache and explicit Brotli/identity routes; r79 routes/assets remain intact. App transfer is approximately 288.5 KiB Brotli, all runtime files 358.3 KiB, within the existing caps. A first release-focused run exposed missing r80 Vercel negotiation routes; those routes were added and the asset checks rerun. This is preparation, not production deployment.

Still required: complete full-suite/CI gates, PostgreSQL migration rehearsal and concurrency verification on the current source, production backup/migration and release verification. Initial join/offer stale-plan snapshots and broader handoff/scheduling review remain to inspect. Physical Here status for scheduled sessions must be based on fresh, privacy-appropriate presence; this work does not infer it from RSVP. Large sessions and actual-device checks remain; PL-14 is not fully verified.


## September 14 release follow-up

Current release candidate: `ee96d38c934eae4b3bb9e868c6da18e68958f434` on `codex/roster-commitment-resume`. PostgreSQL current-source checks: 41 API tests and 5 concurrency tests passed. The operator migration ran twice on a fresh clone of the prior synthetic r79 database; exact existing fields for its user/court/game/roster/message records remained unchanged and `commitment_requested_at` remained NULL.

Release inspection found that the Flask previous-asset allowlist omitted r79. Fixed in1237d00; five added checks verify all prior runtime files return exact identity/Brotli bytes with immutable caching. Fifteen release asset tests passed.

Initial CI34921079956 finished with1754passed,3failed,1PostgreSQL-only skip. Two failures caught a raw50% radius in the new status dot; the third expected the old service-worker cache. Corrected with the shared radius token and updated cache assertion inee96d38;28affected checks passed. The corrected full run34922007591 is still pending completion; poll that exact handle. Run34921469902 predates those corrections and is superseded, but was not cancelled.

No production migration or deployment occurred. Automatic approval review rejected the planned local production backup because deployment authorization did not explicitly cover export of potentially sensitive user records/messages to the specified local destination. A user approval question is pending for `/Users/timothycrowley/pickleball local /tmp/product-audit-r80/production-before-r80.dump`. Do not retry the export or bypass that decision without approval. The rejected combined command did not create the backup or its helper script. The private temporary environment pull is being removed while awaiting approval.

The next mobile account-access refinement is separately saved at `/tmp/thirdshot-mobile-auth-clarity` on `codex/mobile-auth-clarity`; it is not in this candidate. Two expected-failing next-wave reproductions in primary ignored `tmp/product-audit-r81/entry_consent_repro.py` show that a new join and waitlist acceptance can still accept a price changed since viewing. They are outside the release suite and are unfinished audit work.
