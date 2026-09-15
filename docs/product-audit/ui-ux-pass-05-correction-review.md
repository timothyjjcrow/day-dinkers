# UI/UX pass 5 — Reviewing corrections and update history

September 15, 2026. Continued progress on the complete-app UI/UX goal; this does not complete court-detail coverage.

## Easier decisions

Review updates now compares Community now with Proposed. It identifies your pending updates, shows the remaining confirmation requirement and distinguishes operator-reviewed closure reports. Your own pending value offers Withdraw update instead of the ambiguous combination of Confirmed by you and Not right. A competing proposal says which pending value it replaces and uses Use this update.

Withdraw removes only the exact pending field belonging to the current player. Other pending topics and their evidence remain intact; withdrawal does not count as a rejection. Switching to another proposed value records the superseded choice as withdrawn. Partial and final withdrawal history remain available. The existing two-player consensus rule and operator-only closure review are unchanged.

Decisions return updated community values, the remaining review queue and the player's history in the same response. The UI keeps a persistent result and focuses it, rather than relying on a toast and stale history. Fields and actions lock while saving. Stale decisions offer Refresh updates, and a failed initial read provides Try again. Retry restores focus to the review summary.

Your updates now shows compact, expandable rows with the field, date and status. Opening a row reveals Before and Update values and any review note. Missing historical values say Not recorded. Mobile comparisons stack at 320px; narrow history rows give the title, date and status their own space. Cards use neutral borders, and only the proposed value is tinted.

Confirmed fields refresh untouched form controls while preserving unsent edits. The comparison baseline updates so confirmed values are not accidentally resubmitted. Nested object key order no longer creates a false change. The shared form helper exposes immediate draft persistence: after rebasing, the old recovery banner is cleared and any remaining edits are saved with the latest values. A clean dismissal clears redundant drafts.

## Evidence

Before: source fixture 8071. After: a fresh disposable fixture on 8072, using Alex and Sam Discovery and Cedar Park Courts. Login and proposal setup used fixture APIs; decisions described below used the real application. No production data was changed. The browser evidence is in output/product-audit/evidence/ui-ux-pass-05/correction-review in the primary workspace.

- before.png: old proposed-only card, ambiguous own-update actions and expanded status-heavy history.
- comparison390.png: competing $8 and $10 fee proposals and current $5 information. This capture precedes the final neutral card borders and shorter confirmation count.
- error390.png: an observed POST 503 leaves Use this update enabled, retains the proposed comparison and keeps an unsent court-count edit of 9. Earlier automation attempts clicked outside collapsed or moving panels and made no request; they were not counted as action verification.
- result390.png and history390.png: choosing $10 sends only POST /suggestions/decision while the follow-up GET is deliberately unavailable. Fees update to $10 in the editor, count 9 remains unsent, the result receives focus, and history immediately shows $10 Confirmed, $8 Withdrawn and the separate count proposal Pending.
- withdraw390.png: a bounded, abort-aware request hold verified all form controls disabled and dismissal blocked. The request withdrew only num_courts: 6. On completion its history became Withdrawn, other proposals remained and the published court count did not change.
- A later count confirmation preserves the unsent count 9 against the new published count 6. Stored draft fields contain count 9 and the confirmed $10 fee. A full reload restores exactly those values. Restoring count 6 makes the form clean. The final draft-banner cleanup adjustment has focused automated coverage.
- stale390.png: Sam withdraws the closure proposal through the fixture API while Alex still has its card open. Keyboard confirmation receives the real stale-response error; Refresh updates removes the stale card, shows No pending updates and focuses se-pending-label.
- load-error390.png: an injected GET 503 produces Updates unavailable and an inline Try again. Restoring the service and pressing Enter loads four history entries, shows the empty queue and returns focus to the review summary.
- desktop.png: 1280px expanded history. history320dark-final.png and history-text320-final.png: final narrow layout in dark theme and doubled text in light theme. Client and scroll widths both measured 320px. History rows retain a visible expansion arrow. A final helper-margin correction prevents the review summary's focus ring touching the instruction below it.

Final selected frontend, design, correction and open-play checks: **795 passed** in 18.97 seconds. Earlier broader API/frontend run: **1,050 passed**, one PostgreSQL-related test deselected. An intermediate frontend run found an outdated exact return-object assertion after saveDraft was added; that assertion was updated while retaining the original methods, and the final selection passed. New executable checks cover owner-only withdrawal, stale repeat withdrawal, partial history, closure preservation, competing-value replacement, authoritative comparison/history responses, nested-key equality and rebasing without losing unsent edits. Node syntax and whitespace checks pass.

## Remaining coverage

Venue/community source overrides and role-specific contribution entry, operator review screens, structured-hours corrections, dynamic schedule-row draft restoration and account-switch/late-response interactions remain open. History still exposes the latest 20 records; deeper history navigation needs review. Physical phones, assistive technology and PostgreSQL concurrency were not certified by these SQLite/browser checks. Browser checks verified successful confirmation, withdrawal, stale recovery and read/write failures; the Not right API is regression-tested, but its full browser path remains open.

Next inspect remaining court contribution permissions and source presentation, then broaden to creating/editing sessions and host tools. Keep the full-app goal active. Source work remains on codex/mobile-auth-clarity; no release assets, production deployment, database export or migration were performed. Existing release and production approval requirements remain unchanged.
