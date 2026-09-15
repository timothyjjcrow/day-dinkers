# UI/UX pass 5 — Visit sources, management routes and return context

September 15, 2026. Continued progress on the complete-app UI/UX goal.

## What changed

Before you go identifies whether the displayed opening hours come from the venue or community. Access facts retain their existing source groups. The secondary contact links prefer the published venue website/phone and label those destinations accordingly.

Owners, admins and editors see Manage venue details; team viewers see View venue workspace. Regular players and signed-out viewers receive no workspace action. Preview-only venue data suppresses contribution/management entry. Community editing is explicitly labelled Update community details. When venue overrides exist, its editor is titled Community update and says that venue-provided details stay unchanged.

The court-detail API now returns community_hours separately from projected public hours. This fixes a data-source error: a venue's effective hours note previously became the initial community-editor value. Hours note now uses the community baseline, as the existing access editor already does for community_visitor_info. This adds no migration and does not introduce structured-hours corrections.

After a confirmed community change, the same visit sheet refreshes its facts. It keeps the selected tab and scroll context and restores the relevant action's focus. Returning from a venue workspace does the same. Closing the visit sheet then refreshes the original court sheet; it no longer transitions into another court detail stacked above the existing one. A failed refresh keeps the old content with an inline retry. Retry returns focus to the selected tab; stale and account-switched responses are guarded.

Mobile visit sheets now scroll as one page, retaining the sticky heading. The old fixed introduction, tabs and contact footer crowded the information panel at enlarged text sizes. At widths up to 600px, the information and footer flow normally, removing the nested mobile scroller. Desktop retains the contained panels and preserves their scroll position on refresh.

## Evidence

Disposable source fixtures: 8073 before, 8074 after. The new optional DISCOVERY_VENUE_SCENARIOS fixture supplies a published Cedar Park Racquet Center with different venue/community hours and entrance notes. Alex is owner, Sam editor, Casey viewer and Morgan a regular player. Authentication and initial proposal setup used fixture APIs; app interactions used the actual rendered controls. No external contact link was followed and no production user or venue was changed.

Evidence is in output/product-audit/evidence/ui-ux-pass-05/visit-sources in the primary workspace:

- before.png and player390.png show the mixed-source Access panel before/after action labelling and venue contact selection. The latter predates the final single-scroll mobile layout.
- community-editor390.png: Morgan enters Community update with the community north-gate information, not the venue's reception note. The Hours note input was checked directly: it contains the community 7 AM note, while the public Hours tab shows the venue's 8 AM–8 PM schedule and reception note.
- A seeded first fee proposal and Morgan's actual form submission confirm $8 drop-in. Done returns to the existing Access sheet. An injected GET failure leaves the previous facts visible and exposes Try again (refresh-error390.png). Retry updates the fee while keeping venue guest/entrance information and the Access tab. Closing returns to exactly one court dialog, the same court URL and the Fees/access entry button.
- owner-hours390.png and viewer390.png record role-specific entry before the final mobile scrolling adjustment. Owner entry opens the correct venue workspace with editing enabled. Viewer entry opens the same venue with the editing controls disabled. Sam's editor entry also opens the correct editable workspace. Admin, signed-out and preview states have executable role-rendering coverage rather than full browser journeys.
- Returning from the owner workspace with a GET failure keeps Hours selected and focus on court-visit-manage-hours. After the retry-focus fix, successful retry focuses court-visit-tab-hours. The initial check exposed lost focus when the old retry button was removed before fetching; the final observed behavior corrects it.
- Desktop editor return preserves Access, its measured 156px panel scroll and focus on court-visit-manage-fees (desktop.png).
- editor-hours390-final.png and hours320dark-final.png show final mobile layouts in both themes. access-text320.png shows why the previous fixed layout was crowded; access-text320-final.png shows the final single-scroll layout with doubled computed text. The modal's client and scroll widths both measured 320px. The facts remain readable under the sticky heading, while contact actions flow after the content.

Final selected frontend, design, venue-visit and correction checks: **800 passed** in 19.40 seconds. The initial focused set passed 17 checks. New tests verify separate community/effective hours through venue publication changes and management labels across owner/admin/editor/viewer, regular, signed-out and preview contexts. Node syntax and whitespace checks pass. Browser verification covers keyboard entry, owner/editor/viewer routing, source values, confirmed updates, read failure/retry, dialog count, focus and scroll continuity.

## Remaining work

The full court contribution workflow is not complete: structured community hours, dynamic schedule drafts, deep correction history, condition/closure moderation, missing-court flows and account-switch/late-response race tests remain open. Linked provider/contact behavior, physical phones and assistive technology remain unverified. The venue workspace itself still needs its planned complete UI/UX pass; opening it successfully does not certify every manager feature.

Next broaden to creating/editing sessions and host tools, carrying the remaining court states into the coverage plan. The full-app goal remains active. Source changes are on codex/mobile-auth-clarity. Production deployment, release assets and the existing production approval requirements are unchanged; no export or migration was attempted.
