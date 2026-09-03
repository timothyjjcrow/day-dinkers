"""Regression coverage for final calendar, activity, and icon audit closures."""

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from backend.app import db  # noqa: F401 - initialize the shared model registry first
from backend.models import utcnow
from backend.routes.tournaments import _tournament_schedule_estimate


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()


def section(start, end):
    offset = APP.index(start)
    return APP[offset:APP.index(end, offset)]


def tournament(**overrides):
    values = {
        'format': 'single_elim',
        'court_count': 2,
        'match_minutes': 30,
        'max_entries': 8,
        'starts_at': utcnow(),
        'matches': [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_tournament_calendar_estimate_uses_format_field_and_courts():
    start = utcnow()
    duration, end_at = _tournament_schedule_estimate(tournament(starts_at=start))
    assert duration == 120  # 8-slot bracket + bronze across two courts
    assert end_at == start + timedelta(minutes=duration)

    start = utcnow()
    duration, end_at = _tournament_schedule_estimate(tournament(
        format='round_robin', starts_at=start,
    ))
    assert duration == 420  # 7 rounds, two waves per round, 30 minutes
    assert end_at == start + timedelta(minutes=420)


def test_existing_tournament_schedule_is_authoritative_for_calendar_end():
    start = utcnow()
    duration, end_at = _tournament_schedule_estimate(tournament(
        starts_at=start,
        matches=[SimpleNamespace(scheduled_at=start + timedelta(minutes=90))],
    ))
    assert duration == 120
    assert end_at == start + timedelta(minutes=120)


def test_calendar_exports_use_real_game_and_tournament_durations():
    game_ics = section('function gameToIcs', 'function downloadIcs')
    assert 'game.ends_at ? new Date(game.ends_at)' in game_ics
    assert 'Number(game.duration_minutes)' in game_ics
    assert 'game.court_number' in game_ics
    assert "String(game.title || '').trim()" in game_ics

    tournament_ics = section('function tournamentEstimatedDurationMinutes', 'function downloadLeagueIcs')
    assert 't.estimated_duration_minutes' in tournament_ics
    assert 't.estimated_end_at ? new Date(t.estimated_end_at)' in tournament_ics
    assert "t.format === 'round_robin'" in tournament_ics
    assert '4 * 3600e3' not in tournament_ics


def test_activity_has_a_first_class_unread_filter():
    activity = section('async function openActivity', '// ---------- Presence banner ----------')
    assert "['all', 'All'], ['unread', 'Unread']" in activity
    assert "activityFilter === 'unread'" in activity
    assert 'items.filter((notification) => !notification.read)' in activity


def test_toast_calls_do_not_put_emoji_in_live_region_copy():
    toast_lines = [line for line in APP.splitlines() if 'toast(' in line]
    assert not [
        line for line in toast_lines
        if any(ord(char) >= 0x1F000 or 0x2600 <= ord(char) <= 0x27FF for char in line)
    ]
    # Multiline call arguments and helper-fed toast copy must stay semantic too.
    for forbidden in (
        "You're in the game! 🏓",
        'Joined ${playNoun} 🏓',
        'Roster full — game on! 🏓',
        'Your pickup game is ready ⚡',
        'Pickup game started — invite or share to fill it ⚡',
    ):
        assert forbidden not in APP
