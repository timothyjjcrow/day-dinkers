# Roster reconfirmation handoff — paused

Saved at the user’s request on `codex/roster-commitment`. This is unfinished source work and has not been deployed. Production remains on r79; the production release receipt is in `release-r79.md` on main.

## Changes saved

- Separate confirmed players, players who must confirm a changed plan, and reserved places.
- Material session edits request renewed guest confirmation while retaining roster capacity. Exact request markers reject stale confirmations and join retries.
- Confirmation uses schedule locks and conflict checks; changes to later weekly dates leave earlier dates alone.
- Show the current plan and confirmation actions before the roster, with concise status groups and persistent roster disclosures.
- Add nullable `GamePlayer.commitment_requested_at` and the corresponding additive migration.

## Validation completed

- Reconfirmation and edit tests: 11 passed.
- Related roster, reminder, and scheduling tests: 50 passed.
- Legacy attendance/reminder/reschedule tests: 10 passed.
- Real PostgreSQL concurrency tests: 5 passed, including a price edit racing a stale confirmation.
- Frontend sweep initially had 737 passes and 5 outdated assertion failures; updated assertions passed in a focused 105-test rerun.
- Synthetic browser journey: host changed the session price, play style, and court access; guest saw the updated plan and a held place requiring confirmation at 320px.

## Resume before release

The guest has not yet completed the browser confirmation journey. Verify confirmation, host refresh, a second stale edit, keyboard behavior, and enlarged text. The final CSS change fixes dark-mode confirmation-panel contrast but still needs visual reinspection. Review host-handoff consent and stale initial join/offer snapshots; this work does not claim those are solved. Physical arrival is not implemented by RSVP status.

No new frontend bundle was built: assets still reference r79. Build the next immutable release as r80, run the complete release checks, rehearse the additive schema migration, take a fresh private production backup, and verify deployment before claiming this work live. A branch CI manifest failure is possible until the new bundle is built. Do not deploy these unfinished sources as r79.

Synthetic fixture servers and owned browser sessions are being stopped for the pause. Preserve the isolated worktree and local PostgreSQL data directory for resumption. No real production backup is a test fixture.
