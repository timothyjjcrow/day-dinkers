# Pass 6 — Which dates will change?

September 15, 2026. Source follows 8800f47. Pass 6 and the complete-app UI goal remain in progress.

## Improvement

Choosing This and future dates now loads an explicit list of existing scheduled dates before Save becomes available. The first three dates are visible; Show all expands the rest. The selected session is labeled This date. Hosts can recognize the scope without interpreting recurrence IDs or remembering which exceptions were moved.

Stopping repetition marks the other dates Will cancel. The final action states the actual count, for example Save and cancel 5 dates, and explains that the selected date stays scheduled. The resulting session uses the neutral Session dates heading so a stopped series is not described as still weekly. Essential cancellation consequences remain visible beside the action; the default date list stays compact.

The list and server mutation share the same current-scheduled-time boundary, including moved exceptions. A host-only read endpoint returns the dates and a scope token. If their membership, time, status or host changes before saving, the server rejects the stale scope; the editor retains unsaved fields, refreshes and focuses the list, and requires another explicit Save. A future-date change cannot modify dates now owned by another host. Single-date editing remains available.

## Browser evidence

Local synthetic fixture 8077; no production data or external messages. Primary evidence directory: `output/product-audit/evidence/ui-ux-pass-06/edit-scope/`.

- Baseline: `before-editor390.png` is the actual prior-pass editor capture from 8800f47's review, copied from the editing receipt; it is not a reconstructed before screen. That scope choice had no date list.
- Created a private Tue/Thu series ending October 1. Moved its original first occurrence to October 2, then edited September 17. The scope listed all six existing dates in their current time order, including that moved first occurrence.
- While the editor held a title change, a separate real API request moved October 1 to October 3. Save returned edit_dates_changed without applying the title. The refreshed list put October 3 last, retained the title draft and focused the date panel (`scope-stale390.png`). A second explicit Save succeeded; all six titles matched the new title.
- Held the date-list request and switched to This date only. Save became available for that scope; releasing the old response did not reopen the panel. A synthetic 503 left future-date Save disabled with Try again; a real retry restored the list and retained focus (`scope-failure390.png`).
- Selected One time in future-date scope. The list marked five other dates Will cancel and the final action said Save and cancel 5 dates (`scope390-expanded-final.png`, `scope-stop390.png`). Keyboard expansion worked. Native Enter on Save completed the change. The editor closed and the session selector showed five cancelled dates. API read-back confirmed game 9 remained upcoming with recurrence=none; games 8 and 10–13 were cancelled at their existing times. `scope-stop-saved390-final.png` shows the final Session dates heading.
- Visually inspected 320, 390 and desktop layouts, light and dark themes, and independently doubled computed font sizes. No horizontal page overflow at 320; dates and the destructive-action label wrapped. Mobile Save stayed in normal flow. `scope320-large-dark.png` and `scope320-large-save.png` show enlarged text, with vertical scrolling needed. No physical-device or screen-reader verification was performed.
- Visual review caught hidden list rows retaining CSS padding; the hidden-row rule and excess button margin were fixed. Final compact/desktop evidence: `scope320-light-final.png`, `scope390-light-final.png`, `scope-desktop-light-final.png`. Earlier failure, expanded and enlarged-text captures precede only the final compact-spacing adjustment.

## Checks and limits

The focused backend suite covers actual-time ordering, read-only behavior, stale rejection without applying edits, host boundaries, compatibility of single-date edits, and cancellation of exactly the previewed following dates. The first targeted run passed 842 checks. The final broader frontend/design and session edit/recurrence/attendance/detail/reconfirmation run passed 888 checks and failed one release-manifest check (`scope-verification-final.log`): the immutable r80 source hashes do not match the edited readable assets. This mismatch was already present at baseline 8800f47; r80 has not been rebuilt, and this is not a verified release. JavaScript syntax and diff whitespace checks pass.

This list describes existing dates in scope. It is not a complete before/after projection of dates created or removed by changing weekdays or the series end. The token covers date scope, not every editable field or the full recurrence rule, and remains optional for older clients. It is not a transaction-wide content version lock. Role-loss browser behavior, simultaneous changes inside the save transaction, schedule-conflict acknowledgment, uncertain writes, other-player reconfirmation and further host actions remain open.

Continue with schedule-conflict recovery and the remaining host workflows, retaining the need for projected recurrence changes and editing-time court-hours advice. Source only; no release build, database export, migration or production deployment. The existing backup-approval boundary remains unchanged.
