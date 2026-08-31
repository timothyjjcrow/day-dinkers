"""Regression contracts for the shared Find / Start now / Schedule game flow."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(start: str, end: str) -> str:
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_one_shared_flow_exposes_find_start_and_schedule_as_accessible_tabs():
    flow = section("function openGameFlow(options = {})", "async function checkInAndStartRally")

    assert "const allowedModes = ['find', 'start', 'schedule'];" in flow
    assert 'role="tablist" aria-label="Choose what you want to do"' in flow
    for mode, label in (
        ("find", "Find"),
        ("start", "Start now"),
        ("schedule", "Schedule"),
    ):
        assert f'id="game-flow-tab-{mode}"' in flow
        assert f'data-game-flow-mode="{mode}"' in flow
        assert f"> {label}</button>" in flow
    assert 'role="tabpanel" aria-labelledby="game-flow-tab-${mode}"' in flow
    assert "setupTablistKeyboard(modal.querySelector('#game-flow-modes'))" in flow
    assert "One setup, three clear paths. Finding never creates a game." in flow


def test_shared_setup_uses_native_radio_cards_for_type_and_format():
    choices = section("function gameChoiceCardsHtml", "function openGameFlow")
    flow = section("function openGameFlow(options = {})", "async function checkInAndStartRally")

    assert '<fieldset class="game-choice-field" id="${id}">' in choices
    assert '<legend>${esc(legend)}</legend>' in choices
    assert 'input type="radio" name="${name}"' in choices
    assert "id: `${prefix}-type`, name: `${prefix}-type`, legend: 'Game type'" in choices
    assert "value: 'casual'" in choices and "label: 'Casual'" in choices
    assert "value: 'ranked'" in choices and "label: 'Ranked'" in choices
    assert "id: `${prefix}-format`, name: `${prefix}-format`, legend: 'Format'" in choices
    assert "value: 2" in choices and "label: 'Singles'" in choices
    assert "value: 4" in choices and "label: 'Doubles'" in choices
    assert "gameSetupChoicesHtml('game-flow', gameType, maxPlayers)" in flow
    assert 'input[name="game-flow-type"]' in flow
    assert 'input[name="game-flow-format"]' in flow

    assert ".game-choice-option > input:checked + .game-choice-card" in STYLES
    assert ".game-choice-option > input:focus-visible + .game-choice-card" in STYLES


def test_find_only_reads_current_court_games_and_never_starts_one():
    flow = section("function openGameFlow(options = {})", "async function checkInAndStartRally")
    find = flow[flow.index("const loadFindGames = async () => {"):flow.index("const selectCourt = (court) => {")]

    assert "const detail = await api(`/courts/${selected.id}`);" in find
    assert "Array.isArray(detail.now_games) ? detail.now_games" in find
    assert "const exact = openGames.filter" in find
    assert "const alternatives = openGames.filter" in find
    assert "exact.map((game) => gameCardHtml" in find
    assert "alternatives.map((game) => gameCardHtml" in find
    assert "Nothing was created." in find
    assert "data-game-flow-switch-start" in find
    assert "api('/games/rally'" not in find
    assert "method: 'POST'" not in find


def test_start_is_explicit_and_commits_presence_with_the_configured_rally():
    flow = section("function openGameFlow(options = {})", "async function checkInAndStartRally")
    start = flow[flow.index("if (mode !== 'start'"):]

    assert "primary.textContent = mode === 'start'" in flow
    assert "`Join or start ${setupLabel()}`" in flow
    assert "If an identical game is already open" in flow
    assert "Starting now will switch your check-in from" in flow
    assert "if (!await confirmSelectedCourtIsOpen()) return;" in start
    assert "api(`/courts/${selected.id}/checkin`" not in start
    assert "presenceConfirmed: true" in start
    assert "confirmCourtPresence: true" in start
    assert "expectedCourtId: selected.id" in start
    assert "gameType," in start
    assert "maxPlayers," in start

    rally = section("async function startInstantRally", "function continueInstantRallyCall")
    assert "game_type: attempt.gameType" in rally
    assert "max_players: attempt.maxPlayers" in rally
    assert "confirm_court_presence: confirmCourtPresence" in rally
    assert "if (confirmCourtPresence)" in rally
    assert "result.presence.checked_in === true" in rally
    assert "safePositiveId(result.presence.court_id) === expectedCourtId" in rally
    assert "result.presence_confirmed = result.presence_confirmed === true && exactPresence;" in rally
    assert "state.presence = result.presence" in rally
    assert "updatePlayHeader();" in rally
    assert "renderPlay({ useCachedData: true });" in rally


def test_async_start_cannot_mutate_after_mode_change_or_visible_cancellation():
    flow = section("function openGameFlow(options = {})", "async function checkInAndStartRally")

    assert "const actionIsCurrent = (seq, expectedMode)" in flow
    assert "currentOverlayEntry()?.el === modal" in flow
    assert "if (!actionIsCurrent(action, 'start')) return;" in flow
    assert "if (!actionIsCurrent(action, 'schedule')) return;" in flow
    assert "primary.dataset.gameFlowAction = String(action);" in flow
    assert "primary.dataset.gameFlowAction === String(action)" in flow
    assert "lockFlowForCommit();" in flow
    assert "modal.querySelectorAll('button, input')" in flow
    assert "modal._dismissBlocked = () => modal.dataset.gameFlowCommitting === 'true';" in flow
    assert "el._dismissBlocked()) return;" in APP
    assert "function restoreBlockedOverlayTraversal(nav)" in APP
    assert "if (restoreBlockedOverlayTraversal(nav)) return;" in APP
    assert "history.go(forwardSteps);" in APP
    assert "actionSeq += 1;" in flow
    assert "unlockFlowAfterCommit({ completed });" in flow
    assert "`${setupLabel()} is live at ${selected.name}.`" in flow
    assert "result.presence_confirmed" in flow
    assert "`You’re checked in at ${selected.name}.`" in flow
    assert "your check-in at ${selected.name} could not be confirmed" in flow
    assert "modal.querySelector('#game-flow-dismiss').textContent = 'Done';" in flow


def test_programmatic_mode_changes_keep_one_tabbable_selected_tab():
    flow = section("function openGameFlow(options = {})", "async function checkInAndStartRally")

    assert "button.setAttribute('aria-selected', String(active));" in flow
    assert "button.tabIndex = active ? 0 : -1;" in flow


def test_schedule_carries_the_selected_court_type_and_format_into_planner():
    flow = section("function openGameFlow(options = {})", "async function checkInAndStartRally")
    schedule = flow[flow.index("if (mode === 'schedule') {"):flow.index("if (mode !== 'start'")]

    assert "if (!await confirmSelectedCourtIsOpen()) return;" in schedule
    assert "transitionModal(modal, () => openNewGameModal({" in schedule
    assert "court: selected, gameType, maxPlayers, carriedFromGameFlow: true," in schedule
    assert "Continue to date & players" in flow


def test_planner_keeps_type_and_capacity_visible_as_native_radio_cards():
    planner = section("async function openNewGameModal", "async function renderTournaments")

    setup_at = planner.index('<section class="planner-game-setup"')
    advanced_at = planner.index('<details class="planner-advanced')
    assert setup_at < advanced_at
    assert 'id="ng-game-setup-title">Game setup' in planner
    assert "id: 'ng-type', name: 'ng-type', legend: 'Game type'" in planner
    assert "gameCapacityChoicesHtml('ng', presetMaxPlayers)" in planner
    assert '<input type="hidden" id="ng-max" value="${presetMaxPlayers}" />' in planner
    assert '<select id="ng-max"' not in planner
    assert 'input[name="ng-type"]' in planner
    assert 'input[name="ng-capacity"]' in planner
    assert "modal.querySelector('#ng-type').addEventListener('change'" in planner
    assert "modal.querySelector('#ng-capacity').addEventListener('change'" in planner
    assert "const carriedFromGameFlow = plannerOptions.carriedFromGameFlow === true;" in planner
    assert "${carriedFromGameFlow ? '<span class=\"tag\">Setup carried over</span>' : ''}" in planner

    capacity = section("function gameCapacityChoicesHtml", "function openGameFlow")
    assert '<fieldset class="game-choice-field" id="${prefix}-capacity">' in capacity
    assert 'input type="radio" name="${prefix}-capacity"' in capacity
    assert ".game-capacity-grid" in STYLES
    assert ".game-choice-option > input:checked + .game-capacity-card" in STYLES


def test_play_map_and_court_detail_all_enter_the_same_flow_with_context():
    launcher = section("function rallyLauncherHtml", "function playMoreRoutesHtml")
    assert 'data-goto="game-flow"' in launcher
    assert 'data-goto="new-game"' in launcher
    assert "Find or start a game" in launcher

    ctas = section("function setupEmptyStateCtas()", "// ---------- Map / Courts ----------")
    assert "target === 'game-flow'" in ctas
    assert "openGameFlow({ mode: 'find' });" in ctas
    assert "target === 'new-game'" in ctas
    assert "openGameFlow({ mode: 'schedule' });" in ctas
    assert "openGameFlow({ mode: 'schedule', gameType: 'ranked' });" in ctas

    preview = section("function selectCourtOnMap", "function autoCheckInStorageKey")
    assert "data-preview-play>Find or start</button>" in preview
    assert "openGameFlow({ court, mode: 'find' });" in preview
    assert "startInstantRally" not in preview

    detail = section("async function openCourtDetail", "function openCheckInSheet")
    assert 'id="cd-play-now">Find or start a game</button>' in detail
    assert "transitionModal(modal, () => openGameFlow({ court, mode: 'find' }))" in detail
    assert "transitionModal(modal, () => openGameFlow({ court, mode: 'schedule' }))" in detail
    detail_play_handler = detail[
        detail.index("modal.querySelector('#cd-play-now')"):
        detail.index("modal.querySelector('#cd-open-game')")
    ]
    assert "startInstantRally" not in detail_play_handler
