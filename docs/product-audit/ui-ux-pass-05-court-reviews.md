# UI/UX pass 5 — Court reviews

September 14, 2026. Continued work on the full-app UI goal; court detail remains in progress.

## What became easier

Reviews opens directly from the court's top browsing actions into a dedicated reader. The rating, number of ratings and individual comments lead the screen. The previous inline editor before the list and the bottom-of-page reviews disclosure are removed. Writing is a separate short screen: five stars, an optional comment and Post review. Existing authors see Edit your review and their own review first, even when it is older than the first results page.

A small, accessible flag replaces the repeated full-width Report review action. Delete lives inside the author's editor with confirmation. Rating totals update together in the reader, court shortcut and cached court list after posting, editing or deleting. Successful writes use the mutation response directly, eliminating a follow-up fetch that could incorrectly present a committed write as failed.

Mobile cards preserve full names and comments. The reader and editor remain usable at 320px with doubled text; the editor retains native radio selection and large targets. Back protects unsaved changes. Returning from the editor restores focus to Write/Edit your review; pagination moves focus to the first newly loaded review.

## Verification

Source-only synthetic fixture on port 8068 with 20 reviews and Alex Discovery. No real account, court feedback or safety report was changed. The new fixture option is DISCOVERY_REVIEW_SCENARIOS=1 alongside the existing visit and roster options.

- Created Alex's four-star review: 21 ratings, average 4. Edited it to one star: 21 ratings, average 3.8. Deleted it: 20 ratings, average 4. Parent shortcut matched each result.
- Validation focuses a persistent error when no star is selected. Failed POST preserved the selected rating and comment for retry. Discard/Keep editing preserved the draft when requested.
- Delete cancellation and injected DELETE 503 retained the review and correct totals. Successful deletion removed the author's card and restored Write a review.
- With the post-save GET deliberately unavailable, editing still succeeded and made only the POST request. A behavioral regression check verifies that a late page response cannot overwrite a newer committed edit.
- Fresh reader requests return the authenticated author's review independently of pagination. API checks cover older own reviews, another viewer and anonymous identity protection. Anonymous reader rendering retains reviews whose user IDs are intentionally omitted.
- Pagination loaded all 21 reviews once, hid Load more at the end and focused the first newly added card. Initial GET 503 disabled writing until a successful retry; retry restored the list and editing. Browser append-failure recovery remains unverified separately.
- The flag opened Report court review and Back returned to the reader. No report was submitted.
- Visually inspected 390px and 320px mobile, 1280px desktop, light/dark and doubled computed text at 320px. Reader and editor measured 320px layout/scroll widths with enlarged text. Browser simulation does not certify physical phones or assistive technology.

Broad frontend/design, court-detail and new API checks: **781 passed**. After the final return-focus and empty-state cleanup: **47 targeted checks passed**. Node syntax and git whitespace checks pass. Behavioral checks cover mutation totals/cache updates, anonymous rendering and stale pagination. Updated older source assertions reflect the deliberately retired inline layout.

## Before and after

`before-inline.png` is the prior committed review entry: a form ahead of the reviews. It uses the earlier empty fixture, so it is a layout reference, not a same-data comparison. `reader-390.png` shows the new populated reader; `editor-390.png` shows the separated writing step. The other images capture saved, deleted, failed, empty, narrow, desktop, theme and enlarged-text states. Some screenshots precede the final focus-only adjustment, which does not change their visible design.

## Remaining work

Photo upload, category browsing, lightbox and moderation; contribution editing, successful suggestions, confirmations and history/conflicts; richer role-specific provenance. Pass 5 and the full app goal remain open. Final built-asset and cross-feature verification remain required.

This is source work on codex/mobile-auth-clarity, not a production deployment. Release assets were not regenerated; the inherited production backup approval boundary and release/concurrency requirements remain unchanged.
