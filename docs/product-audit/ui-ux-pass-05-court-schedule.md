# UI/UX pass 5 — Court schedule dates and hierarchy

September 14, 2026. Partial court-detail review; the complete-app goal remains active.

## What became easier

A court's schedule now presents each session as a full-width card: time and availability first, then the session title, organizer source, level, price, player names and action. Full sessions retain a quieter waitlist action. At 320px, times and titles no longer compete for the same narrow column. Longer names and doubled text wrap rather than clip.

Three large text navigation buttons and a separate date line became one date bar with previous/next arrows. The center returns to today's seven-day range. A short time-zone label replaces the explanatory paragraph; local court listings retain their qualifier on each row. Registration and access warnings remain explicit.

## Correctness and interaction

The previous unknown-zone response grouped late-evening Portland sessions under the following UTC day while showing Pacific clocks. The query boundaries had the same mismatch. Requests now include the viewer zone; the server uses the confirmed court zone when present and otherwise uses the viewer zone for player dates, clock labels and query boundaries. The default range starts on the date in that zone. No viewer zone is silently saved as the court zone.

Community-listed wall-clock times without a confirmed court zone remain local listings with no invented timestamp. Creating a player plan still requires confirming the court time zone. The map and saved-court previews use the same date/clock semantics. Remote-zone clocks include an abbreviation, while the schedule header uses a readable generic name such as Pacific Time.

Week changes and retry preserve focus. Returning from a session refreshes the court schedule and restores its exact session action. Browser verification exposed the old refresh/frame timing race; focus now follows a stable action identifier rather than a replaced node. A newer sheet or request cannot take that focus.

## Evidence and checks

Disposable SQLite fixture `tests/e2e/fixtures/discovery_server.py`, source assets, port 8066. All accounts and sessions are synthetic. Before image used the earlier port-8065 fixture as Jamie; after used Alex in the fresh fixture. Both show the same seeded full-session rows. No production records were changed.

- Browser: 390px light, 320px light/dark, 320px doubled computed text sizes, and 1280px desktop. Schedule width equaled scroll width: 354/354, 284/284, 284/284 enlarged, and 604/604 desktop.
- Ten future Cedar Park player sessions, noon through 7:30pm, now share Tuesday Sep 15. The old response split the evening onto Wednesday. The default range correctly begins Sep 14 Pacific rather than Sep 15 UTC at the inspection time.
- Next week: empty state, date range and focused next button. Keyboard Enter activates navigation. Back to today restores the populated range.
- Injected schedule-only HTTP 503: visible Retry schedule, focus on heading, retry restores the same requested week; the original fetch implementation was restored.
- Court → Early beginner play (game 1) → Back: correct session details and focus restored to `[data-court-timeline-game="1"]` after the schedule refresh.
- Broad frontend/design plus court-timeline suite: **783 passed** in 17.78s. Subsequent icon/style/copy/renderer checks are recorded in the accompanying targeted logs. Node syntax and diff-whitespace checks pass.
- Backend regression coverage: inclusive/exclusive midnight boundaries, LA spring 23-hour and fall 25-hour days, UTC+14, confirmed-court-zone precedence, explicit UTC fallback, invalid zone rejection, default local date, and unknown community-zone preservation. Existing visibility, reviewed venue data, external-link safety and hours-conflict tests pass.

Images in the companion evidence folder: `before-390.png`, `after-390-light.png`, `after-320-light.png`, `after-320-dark.png`, `after-320-large.png`, `after-320-large-card.png`, `after-1280-light.png`, `error-390.png`. The error image predates the final zone-label wording. Other after images reflect the final visible design; subsequent compact-renderer cleanup removes unreachable branches only.

## Still open

This does not complete court details, map browsing or any other goal pass. Court-detail overall hierarchy, access/hours, photos, reviews, contributions and richer venue/community interactions remain to review. Mixed-source schedules were exercised in API/renderer tests; the pictured browser fixture contains player sessions. Physical phones, screen readers and production built assets were not certified.

Source work remains on `codex/mobile-auth-clarity`. It is not deployed. The release-assets build, inherited reviewed-plan concurrency work and production backup approval boundary remain unchanged. Never treat the source fixture as a production release.
