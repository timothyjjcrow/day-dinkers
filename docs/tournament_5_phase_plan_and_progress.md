# Tournament Integration: 5-Phase Plan + Progress Handoff

Last updated: 2026-02-16

## Purpose

This document captures the agreed 5-phase tournament integration plan and current implementation progress so a new chat can continue execution without losing context.

## Locked Product Decisions

- V1 tournament format: `single_elimination` (with structure left extensible for future formats).
- Tournament matches affect ELO by default (`affects_elo=true`).
- Invite model supports both:
  - `open` tournaments (players can join directly)
  - `invite_only` tournaments (host invites players/friends; users accept/decline)
- Minimum participants and no-show/check-in policy are required.
- Tournament view must show live progression/results and be mobile-friendly.

## Agreed 5-Phase Plan

1. **Phase 1: Backend foundation**
   - Models, migrations, tournament APIs, bracket progression, backend tests.
2. **Phase 2: Ranked tab UI**
   - Tournament list/detail/create/invite/start, live bracket rendering, match flow integration.
3. **Phase 3: Cross-app surfacing**
   - Sessions calendar + map banner + schedule deep-links to Ranked tournament view.
4. **Phase 4: Leaderboard/Profile**
   - Tournament leaderboard and profile tournament results/history.
5. **Phase 5: Hardening**
   - Edge cases, migration checks, regression sweep, performance/polish, rollout readiness.

## Progress Snapshot

- Phase 1: **Completed**
- Phase 2: **Completed** (plus additional UI hardening pass)
- Phase 3: **Completed**
- Phase 4: **Completed**
- Phase 5: **Completed** (baseline hardening and validation complete; optional rollout enhancements tracked separately)

## What Is Already Implemented

### Phase 1 - Backend Foundation (Completed)

- Added tournament models in `backend/models.py`:
  - `Tournament`
  - `TournamentParticipant`
  - `TournamentResult`
- Extended `Match` model for tournament linkage:
  - `tournament_id`
  - `bracket_round`
  - `bracket_slot`
- Added tournament helpers in `backend/routes/ranked/tournaments_helpers.py`:
  - bracket sizing/seeding
  - initial match creation
  - advancement to next rounds
  - finalization/result points
  - tournament serialization helpers
- Added tournament routes in `backend/routes/ranked/tournaments.py`:
  - create/list/detail
  - join/leave/invite/respond
  - check-in/no-show/start/cancel
  - upcoming feed
  - tournament leaderboard/results APIs
- Integrated tournament routes in `backend/routes/ranked/__init__.py`.
- Added lightweight migration coverage in `backend/app.py` for new columns/indexes.
- Integrated tournament behavior into ranked match lifecycle in `backend/routes/ranked/match.py`:
  - conditional ELO application for tournament matches
  - bracket advance/finalize on confirmed results
- Extended court ranked summary in `backend/routes/ranked/views.py` with live/upcoming/completed tournament lists.
- Added baseline lifecycle tests in `tests/test_tournaments.py`.

### Phase 2 - Ranked Tab UI (Completed + hardened)

- Added tournament UI module `frontend/js/ranked_tournaments.js`:
  - tournament cards/panel under Ranked
  - create tournament flow
  - invite player flow (friends + user search)
  - participant list and statuses
  - bracket and results rendering
  - join/respond/check-in/start/no-show actions
  - schedule deep-link open helper
- Integrated tournaments panel into Ranked render in `frontend/js/ranked_render.js`.
- Integrated tournament refreshes into ranked match actions in:
  - `frontend/js/ranked.js`
  - `frontend/js/ranked_modals.js`
- Added mobile/responsive tournament styling in `frontend/css/style.css`.

### Phase 3 - Cross-App Surfacing (Completed)

- Sessions/calendar integration in `frontend/js/sessions.js`:
  - upcoming tournaments merged with open-to-play schedule items
  - client-side visibility filtering support
  - tournament cards deep-link to court Ranked tournament view
- Map banner integration in `frontend/js/map.js`:
  - upcoming tournaments included in next-7-days schedule banner
- Deep-link support from schedule/tournament cards to Ranked tournament view implemented in tournament UI/app flow.

### Phase 4 - Leaderboard/Profile (Completed)

- Added tournament leaderboard section to `frontend/index.html`.
- Added tournament leaderboard loading/rendering in `frontend/js/app.js`.
- Added profile tournament results section in `frontend/js/profile.js`.
- Added backend tournament leaderboard and result endpoints in `backend/routes/ranked/tournaments.py`.
- Notification routing supports tournament actions/deep-links in `frontend/js/app.js`.

## Hardening Work Completed (Phase 5)

- Added withdrawal endpoint:
  - `POST /api/ranked/tournaments/<id>/withdraw`
  - implemented in `backend/routes/ranked/tournaments.py`
- Improved cancel behavior:
  - active matches are cancelled
  - active/invited participants normalized to withdrawn on cancellation
- Enriched tournament leaderboard payload:
  - `best_finish`
  - `win_rate`
  - `points_per_tournament`
  - explicit sort-order metadata
- Improved Ranked tournament UX:
  - inline tournament detail view under Ranked panel (modal fallback retained)
  - withdraw and cancel actions exposed in tournament detail
  - no-show grace minutes exposed in create form
  - improved participant status readability
- Added additional hardening tests in `tests/test_tournaments.py`:
  - withdraw + rejoin flow
  - invite-only decline/re-invite transition
  - mid-bracket cancellation behavior
  - auto-forfeit grace-period enforcement and delayed start

## Known Validation Status

- Python/backend syntax check: passed (`./venv/bin/python -m compileall backend tests`).
- Tournament tests: passed (`./venv/bin/python -m pytest tests/test_tournaments.py -q`) -> `7 passed`.
- Full Python regression suite: passed (`./venv/bin/python -m pytest -q`) -> `94 passed`.
- Frontend JS syntax check: passed (`node --check frontend/js/*.js`).
- Browser smoke pass: passed on `http://localhost:5001`:
  - Ranked tab tournament section renders and opens without critical runtime errors.
  - Tournament surfaces are present in Ranked UI at desktop and mobile viewport (~390x844).
  - Sessions/map surfaces load without critical errors in smoke scope.
  - One non-blocking 404 static asset error observed (no tournament runtime failure).

## Post-Phase-5 Optional Rollout Enhancements (Not Required for Baseline Completion)

- Feature flag gating for tournament surfaces/actions.
- Telemetry/analytics hooks for key tournament events.
- Explicit rollout checklist/signoff artifact.

## Suggested Prompt for Next Chat

"Continue from `docs/tournament_5_phase_plan_and_progress.md`. Implement optional rollout enhancements (feature flags, telemetry hooks, rollout checklist) while keeping current tournament architecture intact."

