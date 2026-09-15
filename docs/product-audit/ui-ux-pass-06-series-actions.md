# Pass 6 — Recurring cancellations and host boundaries

September 15, 2026. Source follows 37a3993. The full-app goal and pass 6 remain in progress.

## Improvement

Recurring cancellation now identifies the session and shows the existing dates that will be cancelled. The first three are visible, with Show all for the rest. The final action names the count, such as Cancel 5 dates. It remains disabled until the list loads. Redundant hero copy and selected-date format tags were removed; the concise consequence states that players are notified and repetition stops. Single-date cancellation accurately says other dates and results stay unchanged.

The list uses the same current-time boundary as editing, including moved exceptions. The cancellation request includes the reviewed scope token; a changed list is rejected before any cancellation. The sheet refreshes and focuses the dates and requires another explicit action. Read failures offer retry. Switching to This date only invalidates pending reads and restores that action. The submitted scope is captured and its radios are disabled while cancellation is pending, so the visible selection cannot diverge from the request.

Series-wide editing, cancellation and host requests now check ownership of both existing following dates and the standing schedule's host. Accepting a host request checks again against the requesting host's authority. This closes the case where a host of one occurrence could stop another host's series, transfer their future sessions, or change its defaults even when no later dates have been generated. Requests can still be declined after ownership changes. Single-date rights remain intact.

Cancellation follows the reviewed dates even if the selected occurrence has recurrence=none but still belongs to a dated series. Legacy weekly rows are still initialized before cancelling only that date, preserving their standing schedule instead of inadvertently stopping repetition.

## Browser evidence

Primary workspace directory: `output/product-audit/evidence/ui-ux-pass-06/series-actions/`. Only synthetic, in-memory data was used.

- `series-cancel-before390.png`: actual previous screen on fixture 8077, without a date list. Fresh fixture 8078 runs the updated ownership/cancellation backend. It predates only the final legacy single-date initialization restoration, which is covered by the backend test.
- Created a Tue/Thu series ending October 6. Moved its first occurrence (game 8) to October 7, then opened September 24 (game 9) for cancellation. Five dates appeared, including the moved first occurrence. `series-cancel-after390.png` and `series-cancel-all390.png` show the compact and expanded list.
- While the sheet was open, moved game 12 from October 6 to October 8 through a real API request. The old cancellation returned edit_dates_changed. The sheet kept the scope, refreshed the list, focused gc-dates and required another Cancel (`series-cancel-stale390.png`). No date was cancelled by that rejected attempt.
- Held a date lookup, selected This date only, and released the old response. Read-back showed the panel hidden and the single-date action enabled. A synthetic 503 disabled future-date cancellation with Try again (`series-cancel-load-failure390.png`). Restoring the real service and retrying focused the list and enabled Cancel 5 dates.
- Inspected 320/390/1280 widths in both themes (`series-cancel320-light.png`, `series-cancel320-dark.png`, `series-cancel-desktop-light.png`, `series-cancel-desktop-dark.png`). With independently doubled computed fonts, page and scroll width were both 320; content and actions remained vertically reachable (`series-cancel-large320.png`, `series-cancel-large320-actions.png`). Final scope-radio styling is in `series-cancel390-final-light.png`. No physical-device or screen-reader verification was performed.
- Held the cancellation transport before server delivery: payload scope was following_dates with a date token, both radios disabled, and the action said Cancelling (`series-cancel-pending390.png`). Automatic approval review timed out before the release tool ran. The one allowed retry found the native request signal had expired; this was not counted as a save. Resolved the artificial hold as a timeout response, restored native fetch, and retried normally through Cancel. The real backend then succeeded (`series-cancel-success390.png`). Read-back confirmed exactly games 8–12 cancelled, preserving their existing timestamps including October 7 and October 8. The synthetic transport hold did not implement native abort propagation; its delayed recovery is not evidence of normal production timeout behavior.
- Created a second series (games 13–17). Sam joined and accepted a real one-date host request for game 17. Morgan then opened game 13's future-date cancellation: the real endpoint returned future_host_changed; the UI explained that another host manages part of the series and disabled cancellation (`series-other-host390.png`). Choosing This date only hid that message and enabled Cancel this date. No dates in this second series were cancelled. `series-own-date390-final.png` shows the final concise single-date explanation.

## Verification and remaining scope

844 targeted frontend/design, editing, recurrence, host-consent and reconfirmation checks passed (`series-scope-final.log`). New regression coverage verifies rejection before mutation/notifications, exact cancellation scope for moved/nonrepeating occurrences, another host's future rows, another host's standing defaults without later rows, ownership drift before acceptance, decline after drift, and legacy single-date cancellation. Positive recurring transfers and existing edit behavior remain covered. JavaScript syntax and diff whitespace checks passed.

The broader run caught three literal corner-radius values introduced in the previous pass's final control polish. They now use shared design tokens; the final run passes. The same recognizable radio treatment was applied to recurrence scope choices. Earlier screenshots predate this control-only polish and the final single-date wording.

Scope tokens remain optional for older API clients; the new browser requires a loaded token for future-date cancellation. They protect current existing-date scope, not all plan content, and are not a complete before/after recurrence projection. Concurrency tests here use SQLite; this is not new PostgreSQL race coverage. No schema migration was needed.

Next review the dates shown when requesting and accepting recurring hosting, including changed plans and membership. Cancellation with waitlist offers, uncertain network outcomes, all role-loss/expiry states, recurrence projections and previously recorded editing/planner gaps remain open. Source only: no release build, database export, migration or production deployment. The existing backup-approval boundary remains unchanged.
