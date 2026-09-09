# Tournament operations — pending local implementation

9 September 2026 UTC. Changes are unshipped. Implementation continued through explicit waitlist offers and individual banner arrival. No production migration or deployment was performed.

## Released baseline versus local behavior

Baseline `42b567c` lets one doubles partner check in the whole entry, generates exact-looking estimated times, edits times without conflict checks, and starts the bracket without a preview. Its bracket already names players, connects advancement, identifies byes, and shows result state; those existing strengths should not be described as missing.

The local branch adds individual self-reported arrival, estimated/called/playing status, a court-based Now/Next board, conflict validation within a tournament, a reviewed delay operation, and a deterministic bracket preview. A personal next-match card names opponents and the court above the detail tabs. Registration shows fees/payment/refund terms and the expected finish; unknown historical fees remain unknown.

## Schema/API contract

Additive fields only; existing team `checked_in_at` remains a legacy report and is never used to infer individual arrivals.

- TournamentEntry: `player1_arrived_at`, `player2_arrived_at` nullable DateTime; `arrival_history` non-null Text with default/server default `[]`.
- TournamentMatch: `play_state` non-null String(16), default/server default `estimated`, enum `estimated|called|playing`; nullable `called_at`, `started_at` DateTime.
- Tournament: `schedule_version` non-null Integer default/server default 0; `schedule_history` non-null Text default/server default `[]`; `rest_minutes` non-null Integer default/server default 5; nullable Integer `entry_fee_cents`; non-null String(80) `payment_method` and String(300) `withdrawal_policy`, each default/server default empty string.
- No new foreign keys or indexes in this slice. Main implementation task owns migration wiring.

Match `scheduled_at` remains an estimate; `called_at` and `started_at` identify actual organizer actions. Terminal `result_state` takes precedence over operational state. Called/Playing cards show the court rather than displaying a contradictory future estimated start beside “Playing now.” Detail includes `can_call_to_court`, `can_start_play`, and `can_edit_schedule`; server validation remains authoritative.

`GET /tournaments/:id/preview` is organizer-only and read-only. It returns seeds, names, byes, times, incomplete-team warnings, `can_start`, and a field fingerprint. The real UI previews before starting and submits this fingerprint to `/start`; changes to the reviewed field require another preview. An event with queued players or held offers also requires the preview fingerprint through the API; its preview discloses that starting closes those requests without entering the players.

`POST /tournaments/:id/matches/:match/play-state` accepts `play_state` and `expected_schedule_version`. The server requires decided opponents/settled feeder matches, and prevents a court or player being called/playing in two matches. A playing match cannot be silently reset or rescheduled.

`POST /tournaments/:id/schedule/delay` accepts minutes (1–240), `expected_schedule_version`, and optional `preview:true`. Preview changes nothing and returns affected-match and notification counts. Confirmed application shifts unplayed, non-playing matches, returns called matches to estimated, and sends one schedule-change notification per recipient. Duplicate/stale application is rejected by version.

`POST /tournaments/:id/checkin` marks the viewer by default; only the organizer may supply another `user_id`. `arrived:false` reverses a report with history retained. Arrival opens two hours before the planned start. Entry serialization exposes individual arrivals, `my_arrived`, and counts such as 1 of 2 here. Partner replacement clears the departed partner’s arrival state.

## Executed checks

- Final focused run: `tests/test_competition*.py tests/test_tournament*.py tests/test_dated_session_interactions_frontend.py`: **72 passed in 13.57s**.
- Broad `test_api.py`, presence and backend-audit regressions plus tournament frontend tests: **268 passed, 1 failed**. The remaining failure is the site-wide tournament banner integration below.
- The seven earlier recurrence/consent API failures were resolved: six old expectations updated to dated/explicit-consent semantics, and one actual instant-rally bug fixed so the departing host can close a rally when no other physical attendee remains. All seven pass.
- JavaScript syntax and diff whitespace checks passed.

New API tests cover read-only preview and bye equivalence, stale preview rejection, individual doubles arrival/undo/authorization, the early-arrival window, court and player overlap, feeder/rest ordering, called/playing court occupancy, versioned delays, and registration terms. Executed Node tests exercise operational labels and the preview-confirm-apply delay handler.

## Browser evidence

`tests/e2e/fixtures/tournament_operations_server.py` ran with an isolated in-memory testing database on port 8056 and synthetic users only.

1. Organizer reviewed the named, seeded four-team bracket before starting. Preview caused no notifications until the explicit Start action.
2. Organizer called semifinal 1, then marked it Playing. The board and bracket changed status together.
3. Delay preview showed **3 matches / 15 minutes / 8 notified players**. After confirmation the three remaining times moved while the playing match stayed unchanged.
4. A separately logged-in participant saw their named opponents and court above the tabs. Their arrival produced **1 of 2 here**, not a whole-team check-in.
5. CUA’s viewport override did not change Chrome’s viewport. A same-origin local iframe harness was used for responsive CSS inspection instead. At an actual 390px content width, document width was also **390px**, personal match top/bottom were **144px/345px**, and Open match, View this round and I’m here controls each measured **44px** high. The screenshot showed readable names and no page-wide overflow. This is responsive-layout evidence, not physical-device/touch verification.
6. Final harness console error list was empty. All four agent-created tabs were closed and the fixture server stopped.

## Later integration and waitlist verification

- Active tournament banner now derives the current player’s arrival and the two-hour arrival window. Focused arrival/banner regressions pass.
- Tournament waitlist stores queued/offered/accepted/left/expired/closed states and append-only status history. Offers reserve capacity without creating an entry. Players explicitly accept or pass; doubles acceptance still needs independent partner consent. Leaving/rejoining moves to the queue’s back. Increased capacity serves queued players first. Reviewed tournament start closes remaining requests/offers with notification.
- Offers allow 24 hours when the event is over 48 hours away, 4 hours when over 6 hours away, otherwise 30 minutes, capped at a future event start. Maintenance traverses all queued events in ordered pages; the regression covers 201 events.
- Additive TournamentWaitlist fields: id; indexed named-FK tournament_id/user_id; String(16) status default/server queued; nullable offered_at/expires_at; Text history default/server []; unique uq_tournament_waitlist. Named FKs tournament_waitlist_tournament_id_fkey and tournament_waitlist_user_id_fkey. Root owns installation.
- API routes: POST/DELETE `/tournaments/:id/waitlist`; POST `/waitlist/respond` with boolean accept. Serializer exposes waitlist_count, held_offer_count, registration_spots_left and my_waitlist. Organizer alone gets the named queue. New notification kind tournament_waitlist_offer routes to the event.
- Browser: full two-player singles event → join waitlist (#1) → organizer removes an entrant → held offer with fee/terms → explicit Accept → registered. There was no automatic signup.
- Browser: open semifinal2 → match detail → Back returned to tournament1, selected round1, target match visible. Executed Node regression targets actual td-matches tab.
- League implementation and verification are recorded in league-round-operations-evidence.md; these are no longer unimplemented tournament follow-up gaps.

## Release boundaries

The final competition recovery slice, partner deadlines/history, global next-match paging, complete doubles recovery browser flow and updated regression results are recorded in [competition-recovery-evidence.md](competition-recovery-evidence.md).

Cross-activity player commitments are now checked through the shared explicit-review service; focused integration checks are tracked by the main task. Internal court/player/feeder/rest conflicts still block invalid tournament schedules. Court allocation is not a venue reservation and does not establish external court availability. Fee/payment/refund text describes organizer terms; no payment processing was added. PostgreSQL concurrency, production migration, asset bundling and deployed behavior remain separate release verification.
