# UI/UX pass 5 — Photo uploads and cover continuity

September 15, 2026. Continued progress on the complete-app UI/UX goal; court detail is still in progress.

## What became easier

Adding a photo now opens a short child form while preserving the gallery, its filter and its scroll context. Previously the editor replaced the gallery markup and supplied another Back without adding button. The new form has one Back action, a bounded full-image preview, optional category and caption, an expandable optional date and one Add photo action. Rounded corners follow the actual image bounds. The large bordered cover explanation and date instructions are replaced by a short cover note. The court name identifies where the photo is going.

Back protects the selected photo and draft. Keep editing preserves all fields; discard returns to Add photo in the original gallery filter. Future capture dates receive an inline field error. The date picker uses the player's current calendar date. During upload, fields and submission lock and Back cannot dismiss the pending request. Failed uploads preserve the image and every field.

The successful response now includes the saved gallery card. The gallery shows that card immediately, updates its count and focuses the new image without a required follow-up GET. Photo state is also updated in the matching court cache. Responses arriving after an account change cannot update the new viewer.

A full gallery shows its limit before file selection. Upload is disabled at the limit; owners with photos see a short removal hint. Removing one makes Add photo available again.

## Covers and API behavior

A community cover points to the selected photo's own URL. Uploading another court view changes that URL; adding a parking or entrance photo leaves a preferred court view in place. Deletion chooses the next eligible cover. Venue-supplied covers remain intact. The legacy mutable cover endpoint now requires revalidation instead of retaining changing bytes for a day. Individual image URLs retain their existing cache policy.

Player deletion, direct moderator removal and removal through a content report share the cover-selection service. This prevents moderator actions from leaving a deleted photo as the court cover. No schema migration was needed.

## Evidence

Before: gallery fixture on port 8069. After: fresh source fixture on port 8070, with eight generated court diagrams and the synthetic Alex Discovery account. The native file chooser opening was suppressed by a browser test hook; the supported browser upload command set the actual file input. PNG decoding/conversion and all form events used the real application. Native OS pickers, physical phones, HEIC and unusual aspect ratios remain unverified. No real user photo or safety report was changed.

- Before/after: upload-before.png and upload-390.png show the same synthetic image in the previous and new form. The old date/copy block and second dismissal button are gone.
- Mobile: 390px and 320px, both themes; desktop 1280px. Doubled computed text at 320px measured 320px client and scroll widths. Date focus scrolling revealed the field fully above the sticky Add photo action. The expanded-date capture includes that settled scroll state. Earlier enlarged-text captures precede the final image-corner framing change; form geometry and focus behavior are unchanged by that adjustment.
- Draft: selected Courts and entered a caption; Keep editing retained both. A 2099 date produced Choose today or an earlier date and focused cap-date. A valid 2026-09-01 date remained after an injected POST 503, with the photo, category and caption and enabled retry.
- Saved result: while GET /courts/1/photos was deliberately unavailable, retry made only POST /courts/1/photo. The gallery immediately showed nine photos, updated the court count and focused saved photo 9. The viewer showed its exact caption, Courts category and Sep 1 capture date separately from Uploaded Today.
- Cancellation: discarding from an Entrance-filtered gallery preserved Entrance, its two images and focus on gal-add. No photo was posted by those discard checks.
- Pending: a bounded, abort-aware fetch hold proved that editing and submission were disabled and Back did not open a confirmation or dismiss the form. Releasing it uploaded a parking photo and focused photo 10. An earlier test hold did not propagate AbortSignal and was reset without a completed upload; it is not evidence of application timeout recovery.
- Cover: after the court-view upload, court detail loaded /api/courts/1/photos/9. A later parking upload preserved that cover. Deleting photo 9 changed the displayed, loaded cover to /api/courts/1/photos/5 and showed the correct remaining count, with focus on cd-gallery.
- Invalid file: a text fixture produced the valid-image error and left the gallery's Add photo button enabled, with no upload form or busy state.
- Capacity: three local API-only setup uploads filled the gallery to 12. Gallery full · 12 photos appeared before file selection. Deleting one owned fixture photo restored Add photo with 11 remaining.

Broad frontend/design, court-detail, upload, moderation and venue-photo checks: **801 passed** in 22.31s. After the capacity-state addition: **17 focused checks passed**. Earlier photo API selection: **19 passed**. Node syntax and whitespace checks pass. New executable checks verify successful mutation handling even if a gallery callback throws, account-change isolation, saved-card metadata, cover identity/priority, legacy revalidation and reported-content cover replacement. The direct moderator test now starts with the removed photo as its cover and checks that the cover clears. Existing source assertions were moved to the new upload helper without removing their form/feedback guarantees.

## Remaining work

Court listing corrections, confirmations, history/conflicts and permission-specific contribution states are next. The current-day Open play header chip can still show too much schedule detail and deserves simplification when completing court-detail coverage. Photo moderation screens themselves, physical-device file selection and broader cross-feature journeys remain open. This receipt does not mark pass 5 complete.

Source work is on codex/mobile-auth-clarity. Release assets were not regenerated and production was not deployed. The inherited production-backup approval boundary and release/concurrency requirements remain unchanged.
