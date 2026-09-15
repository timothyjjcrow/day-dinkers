# Pass 6 — Session creation hierarchy

Source review on September 15, 2026. Pass 6 remains in progress. This is not a production release.

## What became easier

The final step presents the chosen court/time, format, audience, and court access/cost. Play style belongs with format and capacity; its chosen value stays visible after closing those controls. Time summaries include the end time (or no end time) and recurring settings. An overnight end includes its date.

Optional title, court area, rating range, note and description live in More details. Its closed summary indicates which optional sections have values. Access and price remain visible because they affect whether someone can play. No booking is implied by scheduling.

Audience buttons have concise labels and one selected-audience explanation, including private-link access. A known-empty friends list does not offer a disabled Friends choice or an empty invitation panel. Failed friend loading remains a distinct warning. Existing group/community boundaries and invitation controls remain in code.

Removed repeated setup headings, generic where/when instructions, per-button explanations, and the second create-and-get-link submission action. Scheduling a regular private plan opens that newly created game's sharing sheet after successful creation.

The mobile scheduling footer is in normal flow: it cannot cover More details or pinch the usable form with enlarged text. Narrow screens stack access and price; neutral court/time summaries reduce repeated green containers. Pending and unresolved submissions now lock access/cost and Change answers as well as the previously locked sections.

## Evidence and verification

Local source fixture: port 8074, synthetic Morgan account, Cedar Park Courts with venue scenarios enabled. No production writes or external invitations. Evidence lives in the primary workspace at `output/product-audit/evidence/ui-ux-pass-06/session-planner/`.

Before: `planner-time-before390.png`, `planner-final-before390.png` (dark), `planner-final-before390light.png` (light).

After: `planner-new390-light.png`, `planner-new320-light.png`, `planner-after320-dark.png`, `planner-final-after390-light.png` (restored/edited private plan), `planner-after-desktop-light.png`, `planner-new-desktop-dark.png`. The desktop light screenshot includes the tested failure state. `planner-after320-large-top.png` and `planner-after320-large-bottom.png` show independently doubled computed font sizes, with no horizontal overflow and the submit action reachable. The disclosure remains reachable by scrolling in desktop's existing sticky-footer layout.

Browser checks:

- Restored the edited 9 AM draft after reload and reviewed court/time before final step.
- Edited title, court area, style and price with real form controls. Inspected the resulting values and condensed summaries; attempts that did not change values were repeated and not counted as evidence.
- Submitted a private plan by keyboard. A controlled local held POST verified locked access/cost and court/time changes. Released a synthetic 400 response: inline error received focus, fields unlocked, private selection and title remained. `planner-error390.png` records recovery feedback.
- Retried against the real fixture API. Creation opened game 8's private sharing sheet; Back returned to Morning doubles and the host roster. Read-back returned title Morning doubles, Court 2, rotating doubles, private visibility, 800 cents and 90 minutes at 9 AM. `planner-created390.png` records the result. No link was sent.
- Source JavaScript syntax check and 820 tests passed: all frontend/design tests plus game-planning-fields and recurrence-pattern backend suites. Updated legacy assertions for the deliberate single-submit and More details changes. Results in `tests.log`.

## Still open

This browser pass covered an ordinary player without invite candidates. Recheck populated Friends/recent players, community and group creation, ranked singles/doubles, optional-field validation and renewed feed failures in the browser. Those existing contracts retain automated coverage but are not visually certified by this pass.

Next examine time suggestions and recurrence: the fixture's default suggested 6 AM is outside the venue's 8 AM opening, so suggestions need a clearer relationship to known access. Also review resumed-plan banner density, custom duration, multi-day/time-zone summaries and recovery after uncertain creation. Editing, recurring series and host controls remain separate work, followed by cross-feature checks on final built assets.

Physical devices, native mobile keyboards and assistive technology were not exercised. No fresh production build, database export or deployment occurred; the existing production backup approval boundary remains in force.
