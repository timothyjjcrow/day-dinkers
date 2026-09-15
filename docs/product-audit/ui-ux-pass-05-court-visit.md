# UI/UX pass 5 — Court browsing and visit information

September 14, 2026. Continued progress on the full-app goal; court detail is not complete.

## What became easier

The court page now starts with usable browsing options: Photos, Reviews and Share. Photos opens the gallery even when it is empty, where adding a photo remains available. Reviews has a direct shortcut above the long schedule. Condition reporting lives with arrival and current court activity. Closed listings keep a direct Fix listing action in their banner and a Court status disclosure label.

The top court facts no longer repeat fee and opening information from the adjacent controls. Court count, indoor/outdoor and lights wrap without clipping a fact offscreen. The Access control leads to fees and practical visiting information.

Before you go now has four topics: Hours, Access, Facilities and Open play. Opening a specific entry selects that topic without scrolling past unrelated sections. The sheet keeps a stable height, the selected topic scrolls independently, and Directions stays reachable at the bottom. Access facts use one venue/community source label per group rather than repeating it under every field. Facts follow a practical order: eligibility, playing requirements, guests, entrance, parking and accessibility. Facility icons identify listed amenities without inferring that an unlisted amenity is absent.

The Access update action opens its relevant editor fields, including the nested arrival fields. Cancelling returns to the same topic and action. Existing confirmation, draft and publication behavior was retained.

## Evidence

Source-only synthetic fixture on port 8067, `DISCOVERY_VISIT_SCENARIOS=1`, with populated hours, $5 entry, visitor information and facilities. Alex Discovery is a synthetic account. The existing sparse court tests missing information. No real reviews, photos or corrections were posted.

- Before: the Access entry scrolled into a long sheet, leaving the previous Hours content above the requested heading. Repeated source labels filled the arrival list. The main page led with Share, Add photo and Report conditions; an opening-status chip was clipped offscreen.
- After: at 390px the tabs stayed at exactly y=202 and the panel stayed 486px tall when switching from long Access to short Facilities. All four topics were visually inspected.
- Keyboard: selected tab, roving tabindex, Left/Right and Home/End; only the selected panel is visible. At doubled text, Tab reached the scrollable panel and End scrolled it to the remaining access information. Dismissed/inert sheets do not receive delayed tab focus.
- Responsive: 390px and 320px mobile, 1280px desktop, light/dark, doubled computed text sizes at 320px. The enlarged sheet measured 320px wide with 320px scroll width; Directions remained within the viewport. This is browser simulation, not physical-device or assistive-technology certification.
- Missing facts: Access says fees and arrival details are not listed; no invented free-entry claim. Facility rendering retains known facts without inventing amenities.
- Photos: the top shortcut reaches the empty gallery and Add your photo. A schedule-independent photo HTTP 503 showed Try again; restoring the original fetch and retrying reached the gallery. No upload was made.
- Reviews: the top shortcut opens the existing Reviews disclosure and focuses its summary. The remaining form/list hierarchy needs more work.
- Correction: the Access entry opened `se-visiting` and its nested fields with focus on Access, parking & arrival. An earlier cancel check restored focus to `court-visit-correction` in the Access topic. No submission was made.

Broad frontend/design and court-detail checks: **777 passed** in 15.46s. Final closed-listing adjustments: **45 targeted checks passed**; Node syntax and whitespace checks pass. Existing expectations were updated where the intended UX changed: browsing actions, conditional condition-reporting placement, and topic selection instead of scrolling through every section. The dated-play empty-copy expectation in the older court-detail test was brought in line with the prior committed schedule change.

## Files in this evidence folder

`entry-before.png` / `entry-after.png`; `access-before.png` / `access-after.png`; `hours.png`; `facilities.png`; `open-play.png`; `entry-320.png`; `access-320-light.png`; `access-320-dark.png`; `access-320-large.png`; `access-1280.png`; `missing-access-320.png`; `gallery-empty.png`; `gallery-error.png`; `reviews-entry.png`; `correction-fields.png`.

The screenshots show the final visible populated design. Later closed-listing changes affect only the closed state; they are covered by targeted checks, not a new pictured closed-state browser fixture.

## Remaining work

Next: review reading versus writing. The current authenticated review area puts the editor before other people's reviews and still lives after a long court page. Source inspection also shows summary refresh and return-focus behavior after review mutations need an explicit check. Redesign that journey with complete save/update/delete and failure verification.

Photo upload, category browsing, lightbox actions and moderation need their own review. The photo failure shell still uses its generic opening title. Contribution editing remains a long form outside the newly focused access entry; successful suggestions, second confirmations and conflict/history states remain to inspect. Rich official-venue versus community provenance was renderer-tested here, not browser-certified for every role. Review the rest of court detail and map coverage before completing either pass.

Changes are on `codex/mobile-auth-clarity`, not production. Release asset generation and inherited release/concurrency requirements remain open. The production backup approval boundary remains unchanged; no export, migration or deployment occurred.
