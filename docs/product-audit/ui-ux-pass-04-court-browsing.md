# Court map and list navigation: September 14, 2026

Source review followinge00719a on `codex/mobile-auth-clarity`. Pass4 is in progress; not deployed.

## What changed

List results now open court details directly. A separate Show on map action changes to the map and selects that court. The primary result keeps a consistent chevron; map-selection state belongs to its map action. Closing details preserves List mode, the selected court and return focus. This removes the forced list→map→details detour.

The selected map preview now starts with the court name and known location, then court count, indoor/outdoor, lights when listed, and check-in context. It no longer presents an unlabeled distance from map center as if it were travel distance. Address appears when supplied; the fixture's search summaries only supply the city. The list retains its explicit “Distances from map center” scope.

Next play remains accessible, but uses a compact row rather than a full timeline card. It retains the date/time, name, source, level, capacity and price, plus the original game/planning/external-booking action. View court is the main action; Directions and the next-session action are secondary. The large fixed-height panel was removed. Portrait previews leave more map visible, and their scrollable content keeps the action footer reachable. Buttons stack for enlarged text and stay side by side in short landscape viewports.

## Verified

Local in-memory fixture8065, synthetic Jamie, Cedar Park. Browser checks:

- List result→court details→Close returned to List with focus on the same result; map stayed inert while the full list was shown.
- Show on map opened the preview and made the map interactive. Keyboard panning moved the marker while the preview remained selected.
- Save court→Saved filter showed the court with its direct detail and separate map actions.
- Next-session View waitlist opened the intended game. Closing it returned focus to that preview action.
- Browser-only503 on the local court-play request showed the retry state without removing court detail/directions actions. Restoring fetch and Retry restored the compact row. Override restored.
- 390px light,320px light/dark,1280px light desktop,844×390 landscape. Doubled element text at320px in both themes. Fixed a small Directions overflow; final enlarged preview scroll/client width both286. Fixed landscape buttons covering the court name. The small landscape preview still needs scrolling for its next-session facts.

767 frontend/design checks passed in15.85s against final source. Added executable list/map routing and compact next-session context tests, including price/capacity and external booking identity. Existing mobile selection, area non-mutation, marker behavior and late-response tests remain green. JavaScript syntax, whitespace and browser error checks passed.

Evidence: `/Users/timothycrowley/pickleball local /output/product-audit/evidence/ui-ux-pass-04/`. Final390px map/list images show the final location text. Some narrow/enlarged/desktop images precede the final removal of the redundant Selected court label and replacement of ambiguous distance with known location. They document the layout checks; final390px images document the final copy.

## Still open

Broaden to multiple populated courts, long names, filtering/search/pagination, offscreen selection changes and real device/assistive technology. The one-court fixture is not full map certification. The list's location prompt and desktop duplication between selected preview and result remain candidates for the shared navigation pass.

Next inspect court detail, including a possible date/time mismatch: the fixture showed late local-evening sessions under a following-day heading. Confirm against API timestamps and timezone policy before changing it. Street-address availability in slim search results also needs review rather than inventing a value in the preview.

Production prerequisites and new immutable release assets remain outstanding; this source pass does not resolve the backup approval boundary.
