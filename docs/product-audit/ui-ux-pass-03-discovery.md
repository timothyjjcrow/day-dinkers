# Discovery controls: September 14, 2026

Source review on `codex/mobile-auth-clarity`, following a52525e. This is part of pass3, not completion of the whole Play review. Not deployed.

## What became easier

- Nearby games start around y471 instead of y536 at 390px. A session's Join button now fits above the bottom navigation in the checked example.
- One Filters button shows the current distance, level and any date/court choices. Its sheet applies all choices together. Closing it discards pending changes.
- Open spots is a separate pressed-state button. It uses the existing server filter, preserves date/court choices, and can be turned off to see full games and waitlists again. No client-only reordering or pagination shortcut was introduced.
- Empty searches show “No matching games” with recovery buttons, without a paragraph repeating the available actions. Search failures remain distinct from empty results.
- Filter changes restore keyboard focus to their replacement control when rendering removes the old control. Account/navigation guards avoid restoring focus after leaving the feature.
- Distance/level and time fields use adaptive columns. They stack at narrow widths and enlarged text instead of truncating selected distances.

## Verification

Local in-memory fixture at port8065, synthetic Jamie account, Cedar Park sessions. Existing ranked/session/venue records in production were not touched.

Browser checks: 390px dark and light, 320px dark and light, 1280px light desktop; doubled element text in both themes at320px, with no horizontal sheet overflow. Native selects, form buttons, focus return, scrollable lower fields and actions were exercised. Checked radius50 plus level3.0 apply; closing an unsaved radius10 edit retained50. A typed but unselected court was rejected and focused. Level3.5 produced an empty result and Clear search filters restored games. Open spots removed full sessions; the first result had six available spots and retained its separate confirmed/reserved counts.

A temporary, browser-only fetch override returned503 for local aggregate discovery and its nearby fallback. The screen showed “Nearby play did not load,” not an empty search. Restoring fetch and clicking Retry returned sessions with the open-spots choice intact. Browser error inspection reported no uncaught errors. The override was restored.

758 frontend/design tests passed in13.75s, including a new executable sheet check for atomic preferences, unselected court rejection and account changes. JavaScript syntax and whitespace checks passed. Existing timezone, held-capacity and server-query checks remain green.

Evidence directory: `/Users/timothycrowley/pickleball local /output/product-audit/evidence/ui-ux-pass-03/discovery/`. Before/after: `before-390-dark.png`, `open-spots-390-dark.png`, `recovered-390-light.png`. Filter, narrow, enlarged, invalid, empty, failure and desktop images are alongside `frontend-tests.log`. The initial invalid-court image precedes adaptive column stacking; the final filter images show that correction.

## Remaining scope

Review overall Play navigation/creation hierarchy, My plans, weekly sessions, pagination, calendar and first-time discovery. Retry currently recovers content but its own focus replacement deserves a shared feed-control pass. Real device keyboards, screen-reader announcements and actual browser text zoom remain unverified. Radius/level retain their existing account-scoped persistence; other search choices retain their existing in-memory lifetime. Court overrides distance; that rule is explicit in the sheet.

The final immutable production assets still need to be generated and verified as a new release. This source work does not resolve the existing production backup approval boundary or complete pass3.
