# Next: roster and renewed consent (PL-14)

Confirmed source defects after the r79 planner work:

1. Both current-date and following-date edits clear `attending_at` after a court/time/duration change and also clear both reminder markers. `attendance_confirmation_due` requires one of those markers, so a participant can lose confirmation without receiving an actionable reconfirmation state until a later reminder sweep.
2. A roster row displays “Not confirmed yet” using the *viewer's* `attendance_confirmation_due`. The host can therefore see an unconfirmed player as Going without a marker. Per-person state must drive that label.
3. Going counts all roster rows. Automatic recurring reservations, changed commitments and explicit confirmations need distinct states, while every retained place must continue to count against capacity.
4. The confirmation card says the time is getting close even when a future plan has just changed. It can also sit below host/return/detail actions.
5. Price/access/meeting-area edits can materially change what a player accepted; they currently only send update notices. A price change must not silently inherit consent to the old price.

Implementation boundary:

- Add an explicit nullable per-player commitment-change request timestamp rather than repurposing reminder-delivery fields. Preserve old data as unknown. Record it on material plan edits, including following dates. Respect atomic participant locking and current schedule-conflict checks when reconfirming.
- Derive each player's confirmation state and the viewer's action from their own timestamps. Distinguish confirmed, confirmation requested and reserved/unconfirmed. Keep occupancy and waitlist offer counts separate from these labels.
- Expose a prominent Review changed plan / Confirm spot action with current date, court, price and access; preserve leave/skip semantics. Existing notification insertion is not proof of delivery.
- Do not infer Here from RSVP. Physical presence requires fresh, privacy-appropriate evidence at the session court; expiry, revoked location visibility and leaving must remove that indication. Start by auditing existing presence policy rather than exposing a new location audience.
- Compact large rosters with an explicit count and expandable list. Preserve named sides for ranked matches and keep confirmed results separate from upcoming attendance.

Verification: host and multiple participant viewpoints; unchanged title-only edits; price increase/free-to-paid/unknown-to-paid; future-date edits preserving earlier dates; fresh confirmation and stale concurrent confirmation; waitlist offers, automatic recurring places and capacity; expired/hidden physical presence; 320px and large text. This is a plan, not implemented or deployed behavior.

Reproduced against r79 in an isolated API fixture: both free-to-paid editing and moving the date retained the guest’s place while returning `attendance_confirmation_due: false`. The two expected-failing next-wave checks are kept outside the release suite in ignored `tmp/product-audit-r80/reconfirm_repro.py`.
