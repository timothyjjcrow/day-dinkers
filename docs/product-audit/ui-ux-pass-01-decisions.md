# UI/UX pass 1 — waitlist and hosting decisions

September 14, 2026. Second iteration on `codex/mobile-auth-clarity`, after ac1af8e. Source only; no release build or production deployment.

## Changes

- Removed the redundant public waitlist panel from participant detail. The host still has the named queue and offer controls; other players get their relevant queue/offer decision instead of repeated explanations.
- Held offers now have one card: Your spot is ready, a date-aware acceptance deadline, Accept spot and Pass. Held-capacity text is shortened to “spot on hold.” Queued players have a numbered waitlist state and a Leave waitlist action.
- Hosting requests appear after the practical plan facts and before the roster. Scope and reply deadline are separated into readable facts. The named host remains responsible until acceptance, and the copy explicitly says when that host will leave. The sender still has Withdraw request; the recipient has Accept hosting and Decline.
- Shortened invitation helper copy and the leave confirmation. Staying, leaving and rejoining conditions remain explicit. Direct-invitation screens still need their own browser pass.
- Replaced generic “Response saved” with the actual outcome. Acceptance focuses the updated joined/hosting status and avoids a duplicate success toast when the status is visible. Declining hosting preserves the player's place and reports Hosting declined.
- Cancelling an overlap review no longer creates an error banner. It returns focus to the action and announces that the existing plans were kept. Focus callbacks do not follow navigation into another sheet.

## Executed browser and data checks

Restarted the owned discovery fixture on port8064 with current source and fresh synthetic offers. Used Alex, Sam and Cedar Park; no real accounts or production writes.

1. At390px dark, captured before/after waitlist and host-request layouts. The participant's duplicate Waitlist card disappeared and the host decision moved above the roster.
2. At320px light, the offer's buttons stack cleanly. With all computed text doubled, both offer and host-request modal scroll widths remained320px. Acceptance controls were visible and operable with the keyboard. Screenshots include enlarged controls in their actual scroll position.
3. Host changed the held session from free to$12. Accept opened current plan review. Cancel refreshed the price, kept the offer in `offered` state and the roster at one, restored focus to Accept, and showed no stale toast.
4. Accepting the offer encountered the participant's other session. Cancelling this overlap decision returned focus to Accept with no error banner. Explicitly choosing Keep both plans then produced two confirmed players, no queued/held entry and the You're in state. Focus moved to `gs-joined-state`.
5. Declining the hosting request kept Sam as creator and retained both roster members. The request disappeared and the outcome said Hosting declined. A fresh request, explicitly accepted by keyboard, changed the creator to Alex, removed Sam as requested and showed You're hosting with Alex labelled You/Host. No automatic transfer occurred while viewing the request.
6. Opened the shortened non-host leave confirmation at320px. Stay left both roster members intact. Browser error output was empty.

Final frontend/design-token sweep: **753 passed in14.32s**. The first sweep had751 passes and one old combined host/attendance-renderer assertion; the updated test separately verifies the relocated request, escaped names, scope, deadline, departure condition and correct recipient/sender actions. A new executed-handler test covers cancellation versus success focus, navigation protection, exact reviewed-plan payload, and no duplicate success toast. Focused16 checks passed before the final sweep; counts overlap. Syntax/diff checks passed.

Evidence: `/Users/timothycrowley/pickleball local /output/product-audit/evidence/ui-ux-pass-01/decisions/`. Offer screenshots capture the iteration before the final minor “spot on hold” wording adjustment. Hosting and leave screenshots match their final rendered content.

## Remaining work

Pass1 remains in progress. Direct invitations still repeat the inviter above and below the roster; consolidate after inspecting an actual invitation. Inspect large/mixed rosters, the sending host's management tools, queued/expired/passed offers, cancelled/ended sessions and remaining themes/device states. A new-request notification toast was still briefly present after that request had been accepted; reconcile obsolete notifications with the current task in the notifications pass. Do not clear unrelated toasts as a shortcut.

The underlying reviewed-plan backend contract still needs its previously recorded PostgreSQL concurrency and release gates. Source assets are not the inherited immutable r80 bundle. The production database backup approval boundary remains unchanged.
