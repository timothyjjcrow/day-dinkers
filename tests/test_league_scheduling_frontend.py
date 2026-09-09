"""Calendars use actual agreed dates, and every Arrange action uses scheduling."""
import json
from pathlib import Path
import subprocess

APP = (Path(__file__).resolve().parents[1] / 'public/app-v15.js').read_text()


def test_calendar_uses_agreed_matches_without_inventing_repeated_round_dates():
    helper = APP[APP.index('function leagueToIcs('):APP.index('function downloadLeagueIcs(')]
    fixture = {
        'id': 3, 'name': 'Tuesday league', 'status': 'active', 'current_round': 2,
        'round_deadline_at': '2026-09-15T23:00:00Z',
        'matches': [{'id': 7, 'scheduled_at': '2026-09-10T18:00:00Z',
                     'scheduled_duration_minutes': 90, 'schedule_version': 4,
                     'player1': {'display_name': 'Alex'}, 'player2': {'display_name': 'Sam'},
                     'scheduled_court': {'name': 'Community, Courts', 'city': 'Town'}},
                    {'id': 8, 'scheduled_at': None, 'schedule_options': [{'starts_at': '2026-09-11T18:00:00Z'}]}],
    }
    script = "const location={origin:'https://third-shot.test'};\n" + helper + f"console.log(JSON.stringify([leagueToIcs({json.dumps(fixture)}),leagueToIcs({json.dumps(fixture)},{{matchOnly:7}})]));"
    output = subprocess.run(['node', '-e', script], check=True, text=True, capture_output=True)
    all_dates, one_match = json.loads(output.stdout)
    assert all_dates.count('BEGIN:VEVENT') == 2
    assert 'UID:thirdshot-league-match-7@thirdshot.app' in all_dates
    assert 'league-match-8' not in all_dates
    assert 'DTSTART:20260910T180000Z' in all_dates
    assert 'DTEND:20260910T193000Z' in all_dates
    assert 'SEQUENCE:4' in all_dates
    assert 'Community\\, Courts' in all_dates
    assert 'RRULE:' not in all_dates
    assert one_match.count('BEGIN:VEVENT') == 1
    assert 'Round 2 deadline' not in one_match


def test_arrange_actions_use_structured_scheduling_and_scores_are_secondary():
    cards = APP[APP.index('function bindCompetitionCardOpponentActions('):APP.index('function competitionResultStatusHtml(')]
    results = APP[APP.index("modal.querySelectorAll('[data-opponent-propose]')"):APP.index("modal.querySelector('#competition-edit-schedule')")]
    assert 'openLeagueScheduleSheet(parent, match' in cards
    assert 'openLeagueScheduleSheet(liveParent, match' in results
    assert '<summary>Already played?</summary>' in APP
    assert "'Waiting for reply'" in APP and "'Needs a time'" in APP
    assert 'expected_schedule_version: match.schedule_version' in APP
    assert 'id="league-match-calendar"' in APP
