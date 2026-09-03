from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()
CSS = (ROOT / 'public' / 'styles-v15.css').read_text()


def section(start, end):
    return APP[APP.index(start):APP.index(end, APP.index(start))]


def test_select_picker_is_reserved_for_long_top_level_lists():
    enhancer = section('function enhanceAppSelect', 'function disconnectAppSelect')
    assert "const ownerModal = select.closest('.modal-backdrop');" in enhancer
    assert 'if (ownerModal || enabledOptionCount < 7)' in enhancer
    assert "select.dataset.appSelect = 'native-compact'" in enhancer


def test_modal_stack_has_one_dimmer_and_at_most_two_visible_sheets():
    stack = section('function syncModalStack', 'function focusAfterModalChange')
    assert "classList.toggle('is-behind', !active)" in stack
    assert "classList.toggle('is-deep-behind'" in stack
    assert '.modal-backdrop.is-behind' in CSS
    assert 'background-color: transparent' in CSS
    assert '.modal-backdrop.is-deep-behind .modal { visibility: hidden; }' in CSS


def test_planner_groups_recent_players_and_friends_with_bulk_selection():
    planner = section('async function openNewGameModal', 'async function renderTournaments')
    assert "api('/players/recent?limit=8')" in planner
    assert "'Played with recently'" in planner
    assert "'All friends'" in planner
    assert 'availabilityFirst' in planner
    assert 'id="ng-select-visible"' in planner
    assert "selectVisibleButton.textContent = allSelected ? 'Clear visible' : 'Select all visible'" in planner


def test_selected_controls_use_shared_state_tokens():
    for token in (
        '--selected-fill', '--selected-ink', '--selected-border',
        '--choice-selected-fill', '--choice-selected-ink', '--choice-selected-border',
    ):
        assert token in CSS
    for selector in (
        '.map-filters button.active', '.av-chip.active',
        '.court-filter-option.active', '.segmented button.active',
        '.choice-card.selected', '.invite-chip.active',
    ):
        start = CSS.index(selector)
        rule = CSS[start:CSS.index('}', start)]
        assert 'selected-' in rule


def test_rating_help_is_reusable_and_game_detail_uses_it():
    helper = section('function openThirdShotRatingExplainer', '// "Usually plays" availability')
    detail = section('async function openGameScreen', 'function safeNotificationOverlayRoute')
    assert 'Everyone starts at 1200' in helper
    assert 'team’s average match rating' in helper
    assert '72 hours' in helper
    assert 'openThirdShotRatingExplainer({ parentModal: modal })' in detail


def test_bracket_has_visible_round_connectors():
    bracket = section('function bracketHtml', 'function roundRobinHtml')
    assert 'class="bracket-match-slot"' in bracket
    assert '.bracket-round:not(:last-child) .bracket-match-slot::after' in CSS
    assert '.bracket-round:not(:first-child) .bracket-match-slot::before' in CSS


def test_consolidated_badges_keep_installed_app_count_defined():
    badges = section('function renderBadges', 'async function refreshMe')
    assert 'const messagesTotal = state.unreadMessages + state.communityRoomUnread;' in badges
    assert 'const requestTotal = state.pendingRequests + pendingCrewInviteCount;' in badges
    assert 'const appTotal = messagesTotal + requestTotal + state.gamesToConfirm' in badges
    assert 'const appTotal = total +' not in badges
