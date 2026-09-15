# Pass 6 — Host tools and asking another player to host

September 15, 2026. Source follows 4e9367d. This is progress toward the complete-app UI/UX goal, not completion of pass 6.

## What improved

Manage session now presents three readable action rows: Edit session (Time, court & players), Ask a player to host, and Cancel session. It replaces the competing Edit game and Reschedule buttons with the existing full editor, which already handles date/series scope, conflicts, draft recovery and reconfirmation. The duplicate time-only form and handler were removed from the browser code; the backend reschedule endpoint remains for existing clients. Ranked sessions retain match terminology; immediate play still has no time editor.

The host-request sheet names the session and date, then lets the host explicitly choose a current player. No player is selected by default. The button names the recipient, such as Ask Sam Rivera to host. Leave after they accept is optional and the host's continuing responsibility is stated once. Empty rosters show No other players yet and Back to session instead of an empty fieldset, irrelevant checkbox and disabled request button. Handoff/transfer jargon was removed from this entry flow.

Requests use a real form with shared error handling. The selected person, date scope and leave choice are captured before sending; the entire form becomes inert, duplicate submissions and dismissal are blocked while pending, and failure restores the controls and preserves choices. Success finishes the child-sheet dismissal before refreshing the parent. The request itself does not change ownership.

Mobile submit now stays in normal flow so it cannot obscure errors. Selected rows, radio/checkbox states and focus outlines are recognizable in both themes. Avatar initials remain centered, scale with enlarged text and are hidden from accessibility naming because the adjacent full name already identifies the player.

## Actual browser evidence

Primary workspace directory: `output/product-audit/evidence/ui-ux-pass-06/host-tools/`. Fixture 8077 is in-memory and synthetic; no real users were contacted.

- `host-menu-before390.png` and `host-empty-before390.png` show the actual prior menu and empty request sheet. `host-menu-after390-dark.png`, `host-menu320-light.png` and `host-empty-after390-dark.png` show the new hierarchy and empty-state return path.
- Created Sunday doubles (game 15) as Morgan, then joined synthetic Sam and Alex through the actual plan-review API. The picker initially had no selection and a disabled Choose a player action (`host-picker390-dark.png`). Selecting Sam enabled a button naming him; selected Leave after they accept.
- Held the outgoing request before server delivery. It contained target_user_id=2, edit_scope=this_date and leave_on_accept=true; the form was inert and showed Sending request (`host-pending390.png`). Released a synthetic 503. Sam and the leave choice remained selected, controls unlocked and the error received focus. The first failure screenshot exposed a sticky button clipping the error; `host-failure390-final.png` verifies the normal-flow fix.
- Retried against the real backend. The sheet closed and the parent showed Waiting for Sam Rivera to host (`host-request-sent390.png`). Read-back confirmed Morgan remained creator (4), all three players remained joined, and the pending recipient was Sam (2) with leave_on_accept=true.
- Signed in as Sam and inspected the request (`host-recipient390.png`). Accepted through the actual button. The parent changed to You're hosting, Morgan left, Sam and Alex remained, and the request disappeared (`host-accepted390.png`). API read-back confirmed creator=2 and no pending handoff.
- Used the new host's single Edit session action to change the time. The first automation entry of 12:00 retained AM and saved midnight; this was not claimed as noon. Explicitly selected PM and saved again. Final API read-back was September 20 at 19:00Z (noon Pacific), Sam confirmed, Alex still joined with rsvp_status=needs_confirmation. `host-unified-edit-noon390.png` shows noon–1:30 PM and the retained place awaiting reconfirmation.
- Visually inspected 320, 390 and 1280 widths, both themes, and independently doubled computed fonts. Page and scroll width remained 320. `host-picker-large320-final.png` shows wrapped names, centered larger avatars, complete error text and reachable submit. `host-picker320-final-light.png`, `host-picker320-selected-light.png` and `host-picker320-selected-dark.png` verify final unchecked/checked controls. Earlier desktop/large-text images predate only the final control-border polish. The accessible snapshot names the radio Alex Discovery without redundant initials. No physical-device or screen-reader session was used.
- Opened the final single-candidate picker as the new host for visual checks, then closed the browser without sending another request.

## Checks and remaining work

99 focused UI, host-consent and reconfirmation checks passed. The broader final run passed 837 checks (`host-verification-final.log`), covering frontend/design and host consent, reconfirmation, editing and recurrence. Existing tests were updated to expect the single editor instead of the removed reschedule form and to retain cancellation-entry coverage. JavaScript syntax and diff whitespace checks passed. Final radio/checkbox styling was verified in the browser after that test run. The previously documented immutable r80 manifest mismatch is outside this targeted run; no release verification is implied.

Browser coverage here is a one-date host request with leave-on-accept and a subsequent ordinary time edit. Recurring cancellation and host-transfer scope still need exact-date review, moved-exception checks and host-ownership boundaries. Recipient departure/blocking/role loss while the picker is open, concurrent pending requests, expiry, decline/withdrawal, uncertain writes, large rosters, other creator variants and the recipient's changed-plan recovery remain open for end-to-end review. Backend tests cover several of these contracts but do not certify their UI.

Next review recurring cancellation and host-transfer scope and permissions. Retain all earlier editing/planner gaps. Source only: no production asset build, database export, migration or deployment. The existing backup-approval boundary remains unchanged.
