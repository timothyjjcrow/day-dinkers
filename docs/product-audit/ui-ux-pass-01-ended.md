# UI/UX pass 1 — full invitations, waitlists and ended plans

September 14, 2026. Fourth iteration after67eb236. Source only; no release build or production deployment.

## Changes and findings

- Cancelled and expired scheduled sessions keep their saved name as the heading, with a separate Cancelled/Ended status. Previously the name disappeared entirely. Expired sessions still explain whether attendance or a result is missing; signed-up people are not labelled as having played.
- Replaced the generic What now paragraph and nested panel with two direct actions: Find a game nearby and Plan at this court. Removed the duplicate Message the group button while retaining Session chat for participants. This also removes the panel's low-contrast light background in dark mode.
- Full personal invitations now offer Can't make it alongside Join waitlist. No Accept action is shown for unavailable capacity. Existing queued/offered players retain their queue/offer controls rather than an invitation decline that could revoke visibility while leaving a queue record.
- Found and fixed an existing decline failure: the handler used `event.currentTarget` after awaiting confirmation, when it was null. The confirmation closed without sending a request. The handler now retains its button reference, skips commits after navigation, and preserves error recovery.
- Joining or leaving a session waitlist now focuses the updated numbered state or the next Join control. Previously leaving dropped focus onto BODY. Joining no longer repeats the on-screen waitlist status in a toast; leaving retains its outcome message.

## Executed evidence

Expanded the opt-in synthetic discovery fixture atport8065 with full invitation(game10), queued(game11), cancelled(game12), and expired(game13) scenarios. Restarted only the verified owned fixture; all data stayed in disposable in-memory SQLite.

1. Captured cancelled before/after at390px dark,320px light, and1280×900 desktop. Names, dates, signed-up roster and the next action remain visible. No unreadable nested recovery panel or duplicate chat action remains.
2. At320px with computed text doubled, modal scroll width stayed320px; the next-action buttons wrapped. Keyboard Enter on Find a game nearby returned to the Games/Find games view. Plan at this court opened the planner with Cedar Park Courts selected; Back returned to the session. No new session was created.
3. Inspected expired session as an outside player: Morning open play, Ended, missing attendance, historical date and Signed up roster; no Join action. Participant attendance recording remains a separate unverified visual path.
4. Full invitation showed Full, Join waitlist and Can't make it. Cancelling the decline confirmation kept the invitation and restored focus to the decline button. The original acceptance of that confirmation silently did nothing; a fresh API read still showed pending. After the handler fix, the real button completed the decline and returned to browsing. API read showed invitation null, roster2, joined false, queue null. This is actual behavior verification, not a screenshot-only claim.
5. Queue entry focused `gs-waitlist-state` with “#1 on the waitlist”; leaving focused `gs-waitlist`. API read showed no queue and unchanged roster2. Browser errors were empty.

Evidence directory: `/Users/timothycrowley/pickleball local /output/product-audit/evidence/ui-ux-pass-01/ended/`. Images include full-invitation before/after, cancelled before/after in both themes, enlarged text, desktop, expired state and waitlist focus. Screenshots are synthetic browser evidence, not physical-device or assistive-technology certification.

Final frontend/design sweep: **756 passed in13.51s**, saved in `ui-ended-checks-final.log` in that directory. A new executed-handler regression test clears `event.currentTarget` while confirmation is pending and verifies explicit acceptance, cancellation, navigation and request-error recovery. A focus test verifies deferred focus is discarded after navigation or detachment. Existing duplicate-chat source expectations were updated to require the retained chat action. Syntax and diff checks passed.

## Next coverage

Pass1 remains in progress. Remaining visual coverage includes private invitation decline, changed-plan invitation acceptance, expired/passed offers, sending-host management, ended participant attendance and other terminal match states. The new focus helper does not yet cover the separate Play-card waitlist handlers. Continue into first-time entry and discovery next to broaden the complete-app review; these session paths stay open for the final cross-feature verification.

The immutable r80 assets are still behind the readable source. The next release requires new assets and the recorded backend/concurrency gates. The production backup approval boundary is unchanged.
