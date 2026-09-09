# League rounds and late result review — local evidence

9 September 2026 UTC. Unshipped work on `codex/product-audit-implementation`; no production migration or deployment. Baseline is `42b567c`.

## Behavior

- Round standings use confirmed results from that round, separately from accumulated season records. Win = 3 points; played loss = 1; unplayed = 0. Ties use wins then point difference; unresolved ties share a place. No hidden rating tiebreak. Movement requires a clear played leader/last place; withdrawals can combine singleton divisions so remaining players have opponents.
- Closing, advancing and finishing all require a current reviewed fingerprint. Unresolved reports block closing. Closure freezes the original round snapshot and never invents played losses. A planned deadline prompts organizer review instead of silently moving players.
- Availability changes preview affected opponents and appointments. Absence cancels agreed times and preserves appointment/proposal evidence. Returning opens only valid unscheduled matches, never silently rebooks. Withdrawn memberships remain historical but leave active reminders and future draws.
- A played result can be reported after an absence notice. It requires explicit opposing-player agreement or organizer decision and never confirms by timeout.
- A reviewed weather/makeup extension changes the round deadline, increments its version, notifies active players and keeps agreed appointments intact. Calendar deadlines use the same override/version. Void historical matches are not exported as confirmed appointments.
- Closed-round requests preserve the current result until organizer approval. Participants can agree or object. Approval previews season-record changes, the amended round table and final-round championship effects. Existing divisions, later matches and appointments stay as drawn; prior movement is not replayed. Original closure snapshots remain immutable alongside dated amendments. Versioned result events retain requests, responses and decisions. Outsiders do not receive private review evidence.
- Personal match cards name the opponent and show the actual next scheduling/review action. Pending proposals say Choose a time or View proposed times. Scoring sits under Already played. Round terminology stays consistent even after deadline extensions.

## Additive model/API contract

Root task owns schema installation.

- League: nullable `total_rounds`; Integer `round_version` default/server 0; Text `round_history` default/server `[]`; nullable DateTime `round_deadline_override_at`.
- LeagueMember: nullable `unavailable_round`, `withdraw_after_round`, `withdrawn_at`; Text `availability_history` default/server `[]`.
- LeagueMatch: Text `closed_round_review` NOT NULL default/server `{}`. No additional foreign key or index.
- `GET /leagues/:id/round/preview`; POST `/round/close`; legacy `/advance` and `/complete` use the same reviewed-close contract.
- POST `/availability`: action unavailable/available/withdraw/stay; preview then current fingerprint.
- POST `/round/extend`: deadline + reason; preview then fingerprint.
- POST `/matches/:id/closed-review`: request/respond/preview/approve/reject. Approval requires a current fingerprint and `acknowledge_downstream_effect:true`. Fingerprint includes downstream results, schedule versions, participants, membership records, title and closure history.
- Result detail exposes `can_review_closed_round`, private `closed_round_review`, and `can_report_played_after_absence`; league summary exposes `closed_review_action_count`.

## Executed proof

- Focused API/Node run after the first four late-review tests and waitlist changes: **36 passed**.
- Added privacy/action and scheduling-copy regression run: **14 passed**.
- API tests prove immutable closure, unchanged later appointments/draw, stale downstream snapshot rejection, opponent objection, organizer rejection, championship change and repeat correction without double counting. Missing historical membership no longer crashes standings or absence undo.
- On synthetic in-memory fixture port 8056: availability preview listed Alex Chen and Sam Rivera; report-after-absence 11–7 remained awaiting explicit decision and blocked round closure. Organizer supplied a reason and finalized it. After reviewed closure, a historical 7–11 request left 11–7 intact; effects review showed both players' win/loss and point changes and six later matches preserved. Explicit approval changed the recorded score to 7–11.
- Earlier fixture walkthrough verified round/season tabs, reviewed round close, absence/undo, and deadline extension Sep 15→Sep 22 with Alex/Sam's existing appointment unchanged.
- Earlier 390px iframe harness measured league personal card at y110–286, document width390, and 44px buttons. This is CSS evidence, not physical-device verification.

## Boundaries

Cross-activity scheduling uses shared privacy-safe conflict review with explicit acknowledgement. The later broad competition regression passed 171 tests; final recovery and browser evidence are in [competition-recovery-evidence.md](competition-recovery-evidence.md). Shared agenda/calendar/global conflict confirmation belong to root. PostgreSQL concurrency, production schema installation, release bundling and deployment remain separate release checks. A result amendment intentionally does not redraw already-created rounds; the organizer and players see that effect before approval.
