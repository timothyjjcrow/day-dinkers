# UI/UX pass 5 — Court correction topics and receipts

September 15, 2026. Court-detail progress toward the complete-app UI/UX goal; pass 5 remains in progress.

## What changed

Update court opens five collapsed topics: Access & fees, Court setup, Hours note, Open play and Court status. The court name identifies the destination. Review updates and Your updates follow the editing topics. Previously the form exposed almost every field, mixed review work ahead of editing and used long explanatory copy. Court amenities form a compact grid. At 320px, amenities and schedule detail fields use one column; time inputs remain full width. Closure evidence appears only when closure status changes. Ordinary topics no longer inherit a red closure border.

Submission compares normalized current form values with the initial values. Only edited fields are sent, including intentional false and blank values. Unchanged defaults and unrelated topics are omitted. Validation applies to edited court counts and schedules. A closure/reopening request still requires evidence. An unchanged form cannot submit.

The API merges edited topics into the same person's existing pending suggestion, preserving unrelated values and closure evidence. Confirming one field preserves other pending topics and splits the applied history correctly. Suggest and decision endpoints acquire a court row lock; this pass's SQLite checks do not establish PostgreSQL concurrency correctness.

A receipt lists each submitted value and its own Pending, Confirmed or In review status. Confirmed refers to community confirmation; venue-supplied details may still take precedence. Closure reports remain for operator review. Changed listings refresh after the receipt closes, preserving court context and restoring focus to Suggest an edit. Pending initial review responses cannot overwrite the receipt, and response handling checks the original account. Submission disables fields/actions. Failure preserves editable values. Back protects a dirty form; discard clears its draft and returns through overlay history.

The court's current-day Open play chip now shows its time range, or the number of times that day. The full schedule keeps skill level, cost and notes.

## Verification and evidence

Local source fixture port 8071; synthetic Alex and Sam Discovery accounts, Cedar Park Courts. Authentication tokens were established through the fixture API for browser setup. No real listing or user data was changed. Source assets were used; production assets were not rebuilt.

Evidence is in output/product-audit/evidence/ui-ux-pass-05/corrections in the primary workspace:

- before.png shows the old long form. final390light.png and final390.png show the new topic entry in light and dark themes. desktop.png records 1280px layout. setup-final390.png shows the compact amenities and keyboard focus.
- A zero court count focused its field with the 1–100 error. An injected POST 503 sent only {num_courts: 6}; the form retained 6 and restored submission. error390.png records that state before the final neutral-border, spacing and button-contrast adjustments.
- Retrying posted 6 as Alex and showed Pending. Sam independently submitted 6, received Confirmed, then Done refreshed the parent to 6 courts and focused cd-suggest. pending390.png precedes the Done contrast fix; confirmed390.png includes it.
- Blank closure evidence focused se-closure-evidence with an inline error. A bounded, abort-aware request hold verified zero enabled form controls and an active dismissal block. Its body contained only closed and evidence. Releasing it produced In review; review390.png records the receipt. The court remained open.
- Keep editing retained an unsent count of 7. Discard removed the editor, returned focus to cd-suggest and reopening restored the published count rather than the discarded draft. Enter on Court setup expanded it and retained summary focus.
- play320dark.png shows narrow time fields. text320.png and play-text320-final.png use doubled computed text at 320px. Both modal client and scroll widths measured 320px. The latter includes the final one-column Level/Cost change and shows a focused start time above the sticky action.

**1,040 selected frontend, design and API tests passed**, with one PostgreSQL-related test deselected, in 134.95 seconds. The initial focused correction tests also passed (5). New behavioral tests cover unchanged/default omission, false and blank edits, compact schedule summaries retaining full descriptions, independent pending topic merging, partial-confirmation history and closure evidence preservation. Existing presentation assertions were updated to the new labels/markup while preserving their field, validation and decision guarantees. Node syntax and whitespace checks pass. The final narrow Level/Cost CSS adjustment was visually rechecked after the broad run.

## Remaining coverage

Review-update cards, history, withdrawing or replacing conflicting suggestions, mixed venue/community source precedence and permission-specific states still need a deeper pass. Dynamic schedule-row draft restoration, account-switch races and delayed response guards need dedicated interaction coverage. Hours note edits the legacy community note; this does not implement structured weekly-hours corrections. Physical devices, screen readers and real PostgreSQL races remain unverified. These limitations keep court-detail and the wider goal open.

Source changes are on codex/mobile-auth-clarity. No production deployment, database export, migration or release-asset regeneration was performed. The existing production approval and release verification requirements remain unchanged.
