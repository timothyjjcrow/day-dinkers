# Pass 6 — Time selection and weekly setup

September 15, 2026. Source-only changes following 83a17c3. Pass 6 and the full-app goal remain in progress.

## What became easier

The planner previously defaulted to the first half-hour preset after 6 AM, even when the selected venue opened at 8 AM. Defaults now start with common daytime/evening choices. Before offering chips, a bounded read-only hours check filters known conflicts across the selected duration. The same published venue/community projection and interval logic power the court page: split hours, overnight carry and date exceptions retain their established meaning. Missing hours remain unknown, never closed. This does not check booking inventory or reserve a court.

Custom times remain editable and are never silently moved by an asynchronous response. Known conflicts appear beside the time and on the final access/cost step. No matching suggestions gets an explicit empty state. A failed check leaves manual date/time and its draft intact, disables unchecked suggestion chips, and offers retry. Pending advice is visible on both steps. Old court/time/roster/account responses cannot replace newer ones. Retry retains keyboard focus; refreshed suggestion buttons restore focus where applicable. Popularity wording now says Usually busy instead of Good pick.

Weekly setup shows the actual selected days, start time, series time zone and readable end date (or No end date), replacing an explanatory timezone paragraph. Days are in calendar order; default repeat weekdays respect the series zone. The final When summary does not duplicate the start time. Recurring hours advice explicitly says First date: later occurrences are not certified by this check. Hours labels add the court zone when it differs from the browser's zone.

The mobile Next button is in normal flow so it cannot cover the repeat controls. Weekdays fit in one row at normal mobile text sizes and wrap with larger text. Suggested-time cards adapt their column count; a 20-pixel overflow found at doubled text was fixed. Optional end-date input remains an existing native date control.

## Verification

Before screenshot: `recurrence-before390.png`, source fixture 8074. Updated fixture 8075 runs the new backend, synthetic Morgan account and published Cedar Park Racquet Center hours 8 AM–8 PM. Existing fixtures were left running; no production data was touched.

Evidence in the primary workspace: `output/product-audit/evidence/ui-ux-pass-06/scheduling/`.

- Real fixture hours at 9 AM: Venue hours / Open until 8 PM; suggested choices no longer start at 6 AM.
- Manually entered 6 AM: selection preserved, outside-hours warning shown, and 6 AM absent from suggested chips. `scheduling-hours-warning390.png`.
- Saved and restored Tue/Thu, October 1 end date and changed clock after reload. End-date propagation was exercised through the DOM input event after this browser tool failed to fill the native date field; native physical-device date entry remains unverified.
- Held the 7 AM check, changed to 9 AM and accepted its real result, then released the older closed response. The new 9 AM value and Open until 8 PM advice remained unchanged.
- Injected a local 503: all unchecked chips disabled, selected clock and repeat settings retained. Retried by keyboard on final code: real hours returned and focus stayed on the status container. `scheduling-hours-failure390.png` records the earlier failure state.
- Injected an all-conflicting batch on final code: no suggestion chips, explicit empty state, First date warning, and same warning on the review step. The schedule button remained enabled because advice does not revoke custom scheduling. `scheduling-empty390.png`, `scheduling-final-warning390.png`.
- Created an actual synthetic private weekly session via the UI. API read-back for game 8: weekly, Tue/Thu, America/Los_Angeles, 11:00, 90 minutes, through 2026-10-01. Private sharing opened; Back showed the roster and six upcoming dates. `recurrence-created390.png`. No invite link was sent.
- Visual inspection at 390 and 320 widths, light/dark, desktop in both themes, and independently doubled computed text sizes. After the suggestion-grid fix, modal client/scroll width both 320; Next and end date remained reachable. Final screenshots: `recurrence-final390-light.png`, `recurrence-final320-dark.png`, `recurrence-final320-large-top.png`, `recurrence-final320-large-bottom.png`, desktop light/dark. Some earlier screenshots precede the final First date wording; final warning captures include it.
- 828 frontend/design, court advice/timeline and recurrence-pattern tests passed (`schedule-final.log`). New backend coverage checks full-duration split hours, exact closing boundary, unknown days, overnight exceptions, reviewed venue overrides, malformed/oversized batches, auth/unavailable courts and absence of game writes. New frontend coverage verifies series-zone formatting and stale-response guards. 22 targeted checks passed after final wording/empty-state refinements (`schedule-refinement.log`). JavaScript syntax and diff whitespace checks passed.

## Remaining scope

Next: session editing, the distinction between changing one date and future dates, and host tools. Continue browser review for ranked/group/community entry, populated invitations, custom duration invalid values, calendar/keyboard details and older unresolved creation recoveries.

This advice checks the first selected date and a small candidate set, not every future weekly date, available court inventory, or a complete availability search. Review later-date closure handling with recurring-series editing. Court switches/account changes use explicit request guards but were not independently exercised in this browser pass. Server hours rules cover timezone behavior; cross-zone travel and DST in the final browser UI remain open. Date entry on physical mobile devices and screen-reader operation remain unverified.

No release asset build, database export, migration or deployment occurred. The existing production-backup approval boundary is unchanged.
