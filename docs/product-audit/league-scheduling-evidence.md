# League scheduling implementation evidence

Verified locally on 2026-09-08. This records worktree behavior; it is not a production deployment or a claim that the full product audit is complete.

## Audit requirements covered

- **PL-32 — arranging a league match stops at chat:** structured proposals, opponent acceptance, decline, reschedule, cancellation, accepted court/time, calendar events and reminders are implemented.
- **PL-35 — league task order:** match cards lead with Needs a time, Choose a time, Waiting for reply or Scheduled. Score reporting is behind Already played. Arrange opens the scheduling sheet from cards, deep links and the result sheet. Chat remains optional.
- **PL-06 — unified personal schedule, league portion:** `GET /api/leagues/agenda` and `league_agenda_payload(user_id)` expose current participant matches and exact agreed slots. Pending result confirmation/dispute/review remains in the response with existing result action flags. The shared Play integration is owned and verified separately.

## Behavioral contract

`POST /api/leagues/<league>/matches/<match>/schedule/proposals` accepts 1–3 choices of future start, court and duration. `schedule/respond` accepts or declines the current proposal; `schedule/cancel` removes the appointment and options. Each mutation requires `expected_schedule_version`.

Only the two players propose and accept. An organizer can cancel, but cannot agree on behalf of players. Scheduling requires the current active round, an unreported match, both league memberships and no player block. Every option must end before the round deadline. Other users cannot read private time choices or the agreed court/time. A pending change leaves the old appointment intact until accepted.

Player locks and a version-guarded update prevent stale league proposals from overwriting an accepted slot. Known joined games, current league appointments and scheduled tournament matches are checked for overlap again on acceptance. Cross-domain simultaneous writes are not globally serialized by all existing game/tournament writers; this slice does not claim a universal scheduling lock.

LeagueMatch stores `scheduled_at`, `scheduled_court_id`, `scheduled_duration_minutes`, `schedule_version`, `schedule_proposals`, `schedule_proposed_by_id`, `schedule_day_reminded_at`, and `schedule_hour_reminded_at`. Parent owns migration and production verifier wiring.

Calendar download and subscription include accepted appointments with a stable UID and updated sequence. Proposed choices are excluded. Cancellation removes the feed appointment. League exports no longer invent repeating future round deadlines. Round deadlines are identified separately from appointments.

Maintenance calls `send_league_schedule_reminders` for accepted upcoming matches, once within 24 hours and once within one hour per agreed slot. Rescheduling resets the markers; cancellation and closed rounds suppress reminders. Tests verify notification creation and deduplication, not external phone/email delivery or a deployed cron schedule.

## Automated verification

53 tests passed in:

```
python3 -m pytest -q tests/test_league_scheduling.py tests/test_league_scheduling_frontend.py tests/test_competition_detail_completion.py tests/test_competition_detail_etags.py tests/test_competition_completion_frontend.py tests/test_maintenance_cron.py
```

The scheduling suite exercises proposal and consent, preserving agreed plans through declined changes, mandatory/stale versions, outsider privacy, organizer permissions, round closure, cancellation, reported results, departed members, blocking, validation/deadlines, session/league/tournament overlap, pending result agenda state, calendar agreement/change/cancel, and reminder idempotency/reset. The frontend helper executes in Node to verify actual iCalendar dates/duration/escaping, stable UID and version, and exclusion of speculative dates. `node --check public/app-v15.js` passed.

Tests use isolated in-memory SQLite. PostgreSQL row-lock behavior still needs the repository's PostgreSQL integration verification; a sequential stale-write API test alone is not evidence of a real concurrent PostgreSQL race test.

## Browser verification

CUA controlled two synthetic accounts in the isolated testing server on port 8051, using separate localhost/127.0.0.1 browser storage. No real users or production records were used.

1. Alex sent two date/time/court choices; the card changed to Waiting for reply.
2. Jordan saw both options and accepted one; the card showed Scheduled, Thu Sep 10 at 4 PM, Tustin Pickleball.
3. At an explicitly verified 390×844 viewport, the choice sheet and scheduled card were visually inspected. The confirmed sheet showed both players, league/round, local timezone, date, court, duration and Add match to calendar.
4. Jordan proposed a different date. Both players still saw the original Confirmed plan and a separate pending option. Alex declined it; the original appointment stayed Scheduled.
5. Alex opened Already played → Add score → Arrange match. This opened the structured scheduling sheet. Cancelling there returned through the result sheet and back to a match card showing Needs a time.
6. Both browser error logs were empty at the end. Temporary tabs were closed and the viewport override reset.

The calendar file content and reminders were verified by automated tests; the browser run verified the calendar action's presence, not a third-party calendar application's sync timing.

## Activity integration follow-up

Activity now evaluates league schedule replies, league result decisions, and tournament result decisions before pagination. Schedule notifications carry the exact schedule version so superseded proposals do not remain actionable. Reading a request does not resolve it. League membership/block/round state is checked for schedule replies; score review follows current participant/team/organizer permissions and active competition state. A reporter's doubles teammate cannot independently confirm the reporter's score. A general event link is not treated as a match decision.

After this integration, 32 tests passed across `test_league_scheduling.py`, `test_identity_audit_flows.py`, and `test_calendar_and_activity_audit_closures.py`. This includes the updated Unread frontend contract and a regression where an unread item older than 25 read updates is still returned on the first filtered page. This follow-up was API-tested; Activity's rendered view remains part of the identity browser verification.

## Remaining integration work

- Parent owns shared Play agenda rendering, production migration verification and deployment.
- Season standings explanations and absence/replacement management (PL-33/PL-34) are outside this scheduling slice.
