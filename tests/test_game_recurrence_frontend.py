"""Frontend contracts for local recurring schedules and occurrence RSVPs."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()
STYLES = (ROOT / 'public' / 'styles-v15.css').read_text()


def section(start, end):
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_planner_collects_timezone_multi_weekday_pattern_and_end_date():
    planner = section(
        'async function openNewGameModal',
        'async function renderTournaments',
    )

    for contract in (
        'id="ng-recurrence-settings"',
        'id="ng-recurrence-weekdays"',
        'data-recurrence-day="${day}"',
        'type="date" id="ng-recurrence-end"',
        "Intl.DateTimeFormat().resolvedOptions().timeZone",
        'recurrence_timezone: recurrenceTimezone',
        'recurrence_weekdays: [...recurrenceWeekdays]',
        'recurrence_ends_on: recurrenceEndsOn',
        'Repeat days, times, and the end date follow',
        "plannerOptions.recurrence === 'weekly'",
        'Array.isArray(plannerOptions.recurrenceWeekdays)',
        "${initiallyRecurring ? 'checked' : ''}",
    ):
        assert contract in planner

    assert 'Choose at least one repeat day.' in planner
    assert 'Choose an end date on or after the first session.' in planner


def test_draft_sanitizer_and_recovery_keep_the_complete_recurrence_rule():
    draft = section(
        'function sanitizeGameCreatePayload',
        'async function openNewGameModal',
    )
    planner = section(
        'async function openNewGameModal',
        'async function renderTournaments',
    )

    for field in (
        "hasOwnProperty.call(value, 'recurrence_timezone')",
        "hasOwnProperty.call(value, 'recurrence_weekdays')",
        "hasOwnProperty.call(value, 'recurrence_ends_on')",
        'recurrenceTimezone:',
        'recurrenceWeekdays:',
        'recurrenceEndsOn:',
    ):
        assert field in draft

    for recovered in (
        'recurrence_timezone: restoredDraft.recurrenceTimezone',
        'recurrence_weekdays: restoredDraft.recurrenceWeekdays',
        'recurrence_ends_on: restoredDraft.recurrenceEndsOn',
    ):
        assert recovered in planner


def test_host_edit_exposes_and_submits_the_same_local_rule():
    edit = section('function openEditGameSheet', 'function gameFingerprint')

    for contract in (
        'id="eg-recurrence-settings"',
        'id="eg-recurrence-weekdays"',
        'id="eg-recurrence-end"',
        'recurrence_timezone: editRecurrenceTimezone',
        'recurrence_weekdays: [...editRecurrenceDays]',
        'recurrence_ends_on: recurrenceEnd',
        'Repeat days, times, and the end date follow',
    ):
        assert contract in edit

    assert 'Choose at least one repeat day.' in edit
    assert 'Choose an end date on or after the first session.' in edit


def test_detail_has_standing_rsvp_skip_rejoin_and_leave_series_actions():
    detail = section('function gameScreenHtml', 'async function openGameScreen')
    actions = section(
        'async function openGameScreen',
        'function safeNotificationOverlayRoute',
    )

    for contract in (
        'game.recurrence_weekdays',
        'recurrenceClockLabel(game)',
        'game.recurrence_ends_on',
        'game.my_recurrence_rsvp',
        'id="gs-standing-rsvp"',
        'id="gs-skip-occurrence"',
        'id="gs-leave-series"',
        'Skip only this date',
        'Rejoin this date',
    ):
        assert contract in detail

    clock = section('function recurrenceClockLabel', 'function gameScreenHtml')
    assert 'game.recurrence_timezone' in clock

    assert '`/games/${game.id}/recurrence-rsvp`' in actions
    assert '`/games/${game.id}/skip-occurrence`' in actions
    assert "#gs-leave, #gs-not-coming, #gs-leave-series" in actions
    assert "'Leave this series?'" in actions
    assert 'Skip only this date?' in actions
    assert 'standing_rsvp: !standing' in actions


def test_recurrence_controls_have_distinct_responsive_styles():
    for selector in (
        '.planner-recurrence-settings',
        '.recurrence-weekdays',
        '.recurrence-weekdays button.active',
        '.recurrence-rsvp-card',
        '.recurrence-skip-state',
    ):
        assert selector in STYLES
