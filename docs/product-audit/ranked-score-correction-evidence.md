# Ranked score correction and provenance evidence

Verified locally on 2026-09-09 UTC. This records unshipped working-tree behavior for PL-22 and PL-23; it is not production verification or a whole-audit completion claim.

## Result contract

The original match stays unresolved and unrated after a dispute. Its original players have seven days from the first dispute and at most two corrected proposals. Original sides cannot change. Only an opposing player can explicitly agree; a disputed correction never settles automatically. Rejecting the final proposal leaves the match unrated with Result help. A proposed correction expires at the original deadline without extending it.

Existing POST complete/confirm/dispute endpoints take `expected_score_version`. The initial version-zero report remains compatible with legacy clients; subsequent decisions require the displayed version. Locked game rows and version checks prevent stale sequential decisions. Confirming twice cannot apply a second rating change. Late disputes remove the previous rating result before any replacement is agreed. Confirmed players receive attended=true ledger events only when the score is actually finalized.

Game adds `score_version`, `score_history` and `score_correction_pending`; the root task owns schema migration and production verifier wiring. Append-only snapshots preserve actor, time, original sides, score lines and decision reason, including the original automatic result and rating removal. Historical snapshots are visible to existing match participants, not outsiders. Older results are labeled as legacy snapshots; missing historical detail is not invented.

Blocking continues to prevent direct contact and new invitations, while existing match participants retain their commitment and may resolve its score. The API and serializer use that same existing-commitment policy. An earlier checkpoint assumed the discovery helper blocked existing players too; this evidence supersedes that assumption.

## UI corrections

- The editor keeps original players/sides fixed, shows the agreement deadline and sends the exact score version.
- Review identifies who proposed the displayed score and offers Agree or Reject. Confirmed results identify the report and confirmation; Result history keeps each prior version.
- Closed-state copy distinguishes an expired window, exhausted proposals and unavailable correction permission. The final rejection explains that no more proposals remain.
- Result help explains the two-proposal/seven-day rules and provides support access. Initial-score help distinguishes automatic confirmation from corrected-score agreement.
- Two reproduced baseline defects were fixed: score selectors selected the decrement button instead of the numeric input, and dispute handlers lost event.currentTarget while awaiting the reason sheet. Typed score submission and dispute POSTs now work through the actual browser UI.

## Verification

52 tests passed after the final changes across test_ranked_score_corrections, test_ranked_score_corrections_frontend, test_game_detail_manage, test_game_score_validation, test_ranked_streak_rollback, test_game_consent_attendance, test_identity_audit_flows and test_score_entry_frontend. node --check public/app-v15.js passed. A separate 20-test broader score regression selection passed earlier.

API tests cover late automatic rollback and corrected agreement; no timeout settlement or double rating; two rejected proposals; expiry and maintenance; outsider, teammate and original-side permissions; missing/stale versions; immutable history; confirmed-only attendance; and consistent existing-match resolution across blocks without restoring DMs. Executed frontend helpers verify escaped history snapshots, correction deadlines and distinct closed reasons.

Two synthetic accounts used the isolated in-memory server on port 8051. Browser checks completed late dispute → rating removal → same-match 7–11 correction → opposing agreement, with the complete history visible. A second match completed both proposal/rejection cycles, displayed the final-proposal consequence before submission, then showed no further proposal action and opened Result help. Original-side inputs were disabled throughout. Both browser error logs were empty; QA tabs were closed.

A prior correction review was inspected inside an actual 390×844 iframe, with clientWidth and scrollWidth both 390. This verifies responsive layout, not physical phone touch/keyboard behavior. The temporary public fixture was removed. The final second-rejection check used desktop Chrome. The agent-browser CLI was unavailable, so CUA performed browser checks against the already-running server.

## Remaining release checks

PostgreSQL concurrent-transaction verification and production migration/deployment are not covered by isolated SQLite tests. External notification delivery and physical-device accessibility still need integrated release verification. This slice does not add automatic support adjudication after the correction limit.
