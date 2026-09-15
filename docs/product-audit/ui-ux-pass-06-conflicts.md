# Pass 6 — Reviewing overlapping times

September 15, 2026. Source follows 8a02e06. This continues the full-app UI/UX goal; pass 6 remains in progress.

## What improved

The old shared conflict prompt mixed titles and start times into one paragraph, omitted end times, and summarized overlaps beyond the first four without letting the player inspect them. Its Keep both plans label also did not describe cases with several commitments.

The review now compares a highlighted Proposed time range with structured Already scheduled cards. Cards identify a session, league match or tournament match; other players are shown by name with Has another commitment, without exposing their private title, time or link. Estimated times stay labeled. Each proposed date gets its own group, and every returned conflict remains reachable. Batch responses now include proposed duration so those groups can show an end time.

Time overlap replaces the generic Confirm heading and repeated headline. Go back and Keep these times remain clear choices, with one short statement of the consequence. On a host edit, Go back opens Time & court, retains the draft and focuses Start time without treating the decision as a form error. The focus runs after the modal's own return-focus restoration, which otherwise overwrote it.

The layout uses existing app colors, compact labels and clear spacing. At 320 pixels the actions stack; at 390 they share a row. Longer names and enlarged type wrap. The review scrolls in its normal sheet rather than nesting a separately scrolling list.

## Evidence

Primary workspace directory: `output/product-audit/evidence/ui-ux-pass-06/conflicts/`. All data is disposable and synthetic; no external messages were sent.

- `conflict-before390.png`: actual prior UI, triggered by editing Morgan's September 17 Lunch doubles from 1 PM to noon while another session was scheduled 11 AM–12:30 PM.
- `conflict-after390-dark.png`: proposed noon–1:30 PM compared directly with the existing 11 AM–12:30 PM session. The local backend returned a real 409; no time was saved at this point.
- While review was open, a real API request extended the existing session to 1 PM. Accepting the old review triggered another real 409 and a fresh review showing the updated end time (`conflict-stale390.png`). It did not silently accept the changed conflict.
- Go back preserved the noon draft, unlocked the editor, displayed no form error and focused `eg-when-clock` (`conflict-return390-final.png`). An earlier capture exposed the focus being overwritten by modal restoration; the final capture verifies the fix.
- Injected one 503 for the acknowledged PATCH, before it reached the backend. The review closed, the editor kept noon and re-enabled Save with a readable error (`conflict-save-failure390.png`). Restored the real fetch, submitted again, accepted a fresh review and saved successfully (`conflict-save-success390.png`). Read-back confirmed game 14 at 19:00Z for 90 minutes and game 9 at 18:00Z for 120 minutes, both upcoming. The editor's draft after a later cancelled preview was not saved.
- Inspected 320, 390 and 1280-pixel layouts in light/dark themes (`conflict320-light.png`, `conflict320-dark.png`, `conflict-desktop-light.png`, `conflict-desktop-dark.png`). Independently doubled computed font sizes at 320: page and scroll width both 320; content and both actions remained readable (`conflict-large320.png`, `conflict-large320-actions.png`). No physical device or screen reader was used.
- Representative mixed-response browser injection supplied six commitments across two proposed dates, including a long player name, private commitments, a league match and an estimated tournament match. All six cards appeared, the last group and actions were reachable, and an intentionally supplied private title was not rendered (`conflict-mixed390-top.png`, `conflict-mixed390-bottom.png`). Cancelled this preview without saving. This is UI evidence using an injected response, not proof of a real multi-player batch mutation. Fixture 8077 predates the additive batch-duration backend change; that field is verified by the backend test below.

## Verification and remaining work

45 focused checks passed (`conflict-tests-final.log`). These exercise original request/token preservation, refreshed conflict review, cancellation, account/abort boundaries, finite retries, all-card rendering, private-detail suppression, estimated/unknown times, batch duration and changed-token behavior, plus editing/recurrence regressions. Two existing backend tests initially failed because they expected a schedule warning before the newer join-plan review. They now explicitly review the plan first and verify that neither review takes a place prematurely, including held waitlist offers.

827 frontend/design and focused session/scheduling checks passed (`conflict-verification.log`). JavaScript syntax and whitespace checks passed. This targeted run excludes the previously documented immutable r80 manifest/source mismatch; it is not a release verification.

Real browser coverage here is the host's one-date edit. Join/waitlist/competition entry into the shared review, real multi-player recurring edits, very large conflict lists, screen-reader navigation and uncertain network outcomes still need end-to-end coverage. No alternate-time search or full projected recurrence diff was added. These remain part of the full goal, alongside court-hours advice during editing, cancellation, host transfer and other host controls.

Next inspect the host-management menu and its reschedule/cancel/transfer journeys. Source only: no new production assets, database export, migration or deployment. The existing backup-approval boundary remains unchanged.
