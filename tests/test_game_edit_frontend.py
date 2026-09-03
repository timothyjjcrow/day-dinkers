"""Frontend contracts for complete, truthful host game editing."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(start: str, end: str) -> str:
    begin = APP.index(start)
    return APP[begin : APP.index(end, begin)]


def test_host_edit_sheet_exposes_every_supported_field_and_safe_constraints():
    edit = section("function openEditGameSheet", "function gameFingerprint")

    for field in (
        'id="eg-title" maxlength="120"',
        'id="eg-description" maxlength="1000"',
        'id="eg-court-search"',
        'id="eg-when"',
        'id="eg-duration" min="15" max="720"',
        'id="eg-cost" min="0" max="10000"',
        'id="eg-court-number" maxlength="40"',
        'id="eg-court-count" min="1" max="24"',
        'id="eg-capacity"',
        'id="eg-visibility"',
        'id="eg-level-min"',
        'id="eg-level-max"',
        'id="eg-recurrence"',
        'id="eg-recurrence-weekdays"',
        'id="eg-recurrence-end"',
        'id="eg-notes" maxlength="500"',
    ):
        assert field in edit

    assert "exposure.slice(exposure.indexOf(game.visibility))" in edit
    assert "[2, 4].filter((value) => value >= minimumCapacity)" in edit
    assert 'min="${minimumCapacity}" max="${CASUAL_GAME_MAX_PLAYERS}"' in edit
    assert "Group games support up to ${CASUAL_GAME_MAX_PLAYERS} players." in edit
    assert "const canRepeat = game.game_type === 'casual';" in edit
    assert "bindCourtComboboxNavigation(search, results" in edit
    assert "Choose a court from the search results." in edit
    assert "has not already passed" in edit
    assert "Capacity must fit all" in edit
    assert "Use a whole duration from 15 to 720 minutes." in edit
    assert "Enter a cost from $0 to $10,000 with up to two decimals." in edit
    assert "Use a whole number from 1 to 24 courts." in edit


def test_host_edit_sends_only_changed_fields_and_keeps_failures_in_context():
    edit = section("function openEditGameSheet", "function gameFingerprint")

    assert "const payload = Object.fromEntries(" in edit
    assert "value !== initial[key]" in edit
    assert "if (!Object.keys(payload).length)" in edit
    assert "toast('No game details changed')" in edit
    assert "api(`/games/${game.id}`," in edit
    assert "method: 'PATCH', body: JSON.stringify(payload)" in edit
    assert "formUX.startSubmitting('Saving changes…')" in edit
    assert "formUX.showError(error.message)" in edit
    assert "state.playGamesCache = null" in edit
    assert "bindModalDiscardConfirmation(sheet" in edit


def test_hosts_get_direct_manage_actions_and_share_stays_in_the_header():
    detail = section("function gameScreenHtml", "async function openGameScreen")
    screen = section("async function openGameScreen", "function safeNotificationOverlayRoute")

    assert 'class="game-host-toolbar" aria-label="Host tools"' in detail
    assert "You’re hosting" in detail
    for action_id in ("gs-edit", "gs-reschedule", "gs-share", "gs-cancel"):
        assert f'id="{action_id}"' in detail
    assert 'id="gs-share-header"' in detail
    assert "querySelectorAll('#gs-share, #gs-share-header')" in screen
    assert "querySelector('#gs-edit')" in screen
    assert "openEditGameSheet(game" in screen
    assert "Players keep their spot and will be asked to re-confirm for the new time." in screen

    assert ".game-host-toolbar" in STYLES
    assert ".game-host-toolbar-actions" in STYLES


def test_game_detail_refreshes_when_any_editable_field_changes():
    fingerprint = section("function gameFingerprint", "function gameScreenHtml")

    for expression in (
        "game.creator_id",
        "game.scheduled_at",
        "game.visibility",
        "game.preferred_level",
        "game.title",
        "game.description",
        "game.duration_minutes",
        "game.ends_at",
        "game.cost_cents",
        "game.court_number",
        "game.court_count",
        "game.notes",
        "game.recurrence",
        "game.recurrence_timezone",
        "game.recurrence_local_time",
        "game.recurrence_weekdays",
        "game.recurrence_ends_on",
        "game.recurrence_occurrence_on",
        "game.my_recurrence_rsvp",
        "game.court && [game.court.id, game.court.name, game.court.city]",
    ):
        assert expression in fingerprint
