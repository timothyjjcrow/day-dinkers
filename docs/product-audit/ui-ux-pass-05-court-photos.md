# UI/UX pass 5 — Court gallery and photo viewer

September 14–15, 2026. Concrete progress on court detail; pass 5 and the complete-app goal remain in progress.

## What became easier

The gallery is a two-column mobile grid and three-column desktop grid. Previously it displayed nearly one image at a time in a horizontal strip, with dates, reactions and report/delete controls squeezed into each caption. The new grid shows the image, a short title and ownership where relevant. Add photo stays near the top. The photo category selector and matching count are grouped above the grid.

The viewer contains the full image, caption, category, provided date taken, uploader and explicitly labelled upload date. Missing capture dates no longer create a repeated sentence on every photo. Hearts, reporting and ownership deletion live with the opened image. Reporting uses an accessible flag; previous/next have large icon buttons and a clear position count. The viewer navigates only the selected category. Back restores the filter and a usable grid target, including when that photo was deleted.

Deletion now locks conflicting navigation and actions, restores controls after cancellation/failure, and removes the confirmed photo by identity. A failed delete stays visible beside an enabled retry action. Deleting the last photo in a category returns to the remaining gallery; deleting the final photo returns to Add photo. The court count updates and returning to court detail refreshes its content with focus on Photos. Reactions ignore responses belonging to a prior signed-in account.

## Evidence and checks

Disposable source fixture on port 8069; synthetic Alex Discovery account. DISCOVERY_PHOTO_SCENARIOS=1 seeds eight generated court diagrams across four categories and owners. These images are labelled synthetic and are not photos of real venues. No real user content or safety report was changed.

- Before and after: gallery-before.png versus gallery-after-8-dark.png show the same eight fixture images in the old strip and new grid. Later captures show seven after an intentional deletion. viewer-before.png versus viewer-after.png show the viewer hierarchy, with different selected images/filters as labelled by the screens.
- Mobile: 390px and 320px, light/dark gallery; 320px viewer; 1280px gallery/viewer. Precisely doubled computed text at 320px measured 320px client and scroll widths in both screens. Full captions remain readable; content can scroll vertically. No physical-device or assistive-technology certification is claimed.
- Filtering: Courts showed two photos; viewer read 1 of 2 and stayed within that category. ArrowRight advanced the full gallery viewer to 2 of 7 with focus on Next photo. Back kept the selected category.
- Mutation: liked an owned photo, then injected DELETE 503. The photo, reaction and enabled delete action survived. Restoring fetch and deleting removed only that photo, changed court count from eight to seven, and returned to the remaining filtered photo with focus. Deleting the remaining court-category photo reset the gallery to All photos, with six remaining.
- Final-photo focus: two temporary single-photo cases on the empty second court were seeded through the local API, not through the upload UI. The final verified source uses history-aware dismissal and a connected replacement focus target. After deleting the final photo, no viewer remained, the court count cleared and focus was gal-add. Returning to the refreshed court after an earlier category deletion focused cd-gallery.
- Failure: initial gallery GET 503 retained the Photos heading and Try again. Restoring fetch and retrying returned all seven then-current photos. An intentionally missing image URL displayed Photo unavailable while navigation remained usable.
- Safety entry: Report court photo opened from the flag; Back returned without sending a report.
- Empty: the second court showed No photos yet and Add photo, without a filter or invented count.

Broad frontend/design, court-detail and photo API suite: **793 passed** in 17.79s. Initial design checks caught two hard-coded corner radii, corrected to existing shared tokens. Final focus changes: **16 targeted checks passed**. Node syntax and whitespace checks pass. New executable regressions cover cancel restoring controls, navigation during an in-flight deletion, confirmed-photo identity, and late like responses after an account change. Existing source assertions were updated for actions moving into the viewer.

## Remaining work

The upload editor still needs a full UI and behavior pass: preview sizing, category/date fields, shorter copy, draft/cancel handling, successful upload, failed upload/retry and return context. This pass moved its gallery entry but did not claim to verify that form. Cover selection and browser caching after replacing or deleting a non-final cover need explicit verification; current shared cover responses use a long cache lifetime. Photo moderation beyond opening/cancelling the report entry remains open. Contribution editing and the other pending court/map states also remain.

This is source work on codex/mobile-auth-clarity. Production was not deployed, release assets were not rebuilt, and the inherited production-backup approval boundary remains unchanged.
