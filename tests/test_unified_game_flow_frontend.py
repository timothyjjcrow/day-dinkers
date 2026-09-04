"""Regression contracts for live ranked discovery and the shared planner."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(start: str, end: str) -> str:
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_live_ranked_flow_exposes_only_find_and_start_as_accessible_tabs():
    flow = section("function openGameFlow(options = {})", "async function checkInAndStartRally")

    assert "const allowedModes = ['find', 'start'];" in flow
    assert 'role="tablist" aria-label="Choose what you want to do"' in flow
    for mode, label in (
        ("find", "Find"),
        ("start", "Start now"),
    ):
        assert f'id="game-flow-tab-{mode}"' in flow
        assert f'data-game-flow-mode="{mode}"' in flow
        assert f"> {label}</button>" in flow
    assert 'role="tabpanel" aria-labelledby="game-flow-tab-${mode}"' in flow
    assert "setupTablistKeyboard(modal.querySelector('#game-flow-modes'))" in flow
    assert 'id="game-flow-tab-schedule"' not in flow
    assert 'id="game-flow-plan-later"' in flow
    assert "Plan a ranked match for later" in flow
    assert "Plan a ranked match for later" in flow
    assert "Planning for later uses the same full planner everywhere." not in flow
    assert "Find or start a game" not in flow


def test_shared_setup_uses_native_radio_cards_for_type_and_format():
    choices = section("function gameChoiceCardsHtml", "function openGameFlow")
    flow = section("function openGameFlow(options = {})", "async function checkInAndStartRally")

    assert '<fieldset class="game-choice-field" id="${id}">' in choices
    assert '<legend>${esc(legend)}</legend>' in choices
    assert 'input type="radio" name="${name}"' in choices
    assert "const setupLabel = lockedType === 'ranked' ? 'Ranked match setup' : 'Game setup'" in choices
    assert "const typeLegend = lockedType === 'ranked' ? 'Match type' : 'Game type'" in choices
    assert "id: `${prefix}-type`, name: `${prefix}-type`, legend: typeLegend" in choices
    assert "value: 'casual'" in choices and "label: 'Casual'" in choices
    assert "value: 'ranked'" in choices and "label: 'Ranked'" in choices
    assert "id: `${prefix}-format`, name: `${prefix}-format`, legend: 'Format'" in choices
    assert "value: 2" in choices and "label: 'Singles'" in choices
    assert "value: 4" in choices and "label: 'Doubles'" in choices
    assert "gameSetupChoicesHtml('game-flow', gameType, maxPlayers, lockedGameType)" in flow
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
    assert "No open" in find
    assert "data-game-flow-switch-start" in find
    assert "api('/games/rally'" not in find
    assert "method: 'POST'" not in find


def test_start_requires_explicit_checkin_before_the_configured_rally():
    flow = section("function openGameFlow(options = {})", "async function checkInAndStartRally")
    start = flow[flow.index("if (mode !== 'start'"):]

    assert "`${CTA_LABELS.start} ${setupLabel()}`" in flow
    assert "If an identical game is already open" in flow
    assert "Check in at ${selected.name} first." in flow
    assert "Check in to start · ${setupLabel()}" in flow
    assert "if (!await confirmSelectedCourtIsOpen()) return;" in start
    assert "api(`/courts/${selected.id}/checkin`" not in start
    assert "if (!isCheckedInAtCourt(selected.id)" in start
    assert "|| !currentInstantRallyPresenceProof(selected.id))" in start
    assert "openChildModal(modal, () => openCheckInSheet(checkInCourt" in start
    assert "{ defaultLooking: true, presenceIntent: 'instant_rally' }" in start
    assert "primary.click();" in start
    assert "presenceConfirmed" not in start
    assert "confirmCourtPresence" not in start
    assert "expectedCourtId: selected.id" in start
    assert "gameType," in start
    assert "maxPlayers," in start

    rally = section("async function startInstantRally", "function continueInstantRallyCall")
    assert "game_type: attempt.gameType" in rally
    assert "max_players: attempt.maxPlayers" in rally
    assert "presence_proof: presenceProof" in rally
    assert "const presenceProof = currentInstantRallyPresenceProof(expectedCourtId);" in rally
    assert "if (!isCheckedInAtCourt(expectedCourtId))" in rally
    assert "confirm_court_presence" not in rally
    assert "confirmCourtPresence" not in rally
    assert "presenceConfirmed" not in rally


def test_async_start_cannot_mutate_after_mode_change_or_visible_cancellation():
    flow = section("function openGameFlow(options = {})", "async function checkInAndStartRally")

    assert "const actionIsCurrent = (seq, expectedMode)" in flow
    assert "currentOverlayEntry()?.el === modal" in flow
    assert "if (!actionIsCurrent(action, 'start')) return;" in flow
    assert "actionIsCurrent(action, 'schedule')" not in flow
    assert "primary.dataset.gameFlowAction = String(action);" in flow
    assert "primary.dataset.gameFlowAction === String(action)" in flow
    assert "lockFlowForCommit();" in flow
    assert "modal.querySelectorAll('button, input')" in flow
    assert "modal._dismissBlocked = () => modal.dataset.gameFlowCommitting === 'true';" in flow
    assert "typeof el._dismissBlocked === 'function' && el._dismissBlocked()" in APP
    assert "el._onDismissBlocked?.();" in APP
    assert "function restoreBlockedOverlayTraversal(nav)" in APP
    assert "if (restoreBlockedOverlayTraversal(nav)) return;" in APP
    assert "history.go(forwardSteps);" in APP
    assert "actionSeq += 1;" in flow
    assert "unlockFlowAfterCommit();" in flow
    assert "showInstantRallyManagement" not in flow


def test_programmatic_mode_changes_keep_one_tabbable_selected_tab():
    flow = section("function openGameFlow(options = {})", "async function checkInAndStartRally")

    assert "button.setAttribute('aria-selected', String(active));" in flow
    assert "button.tabIndex = active ? 0 : -1;" in flow


def test_plan_later_routes_directly_to_the_single_full_planner():
    flow = section("function openGameFlow(options = {})", "async function checkInAndStartRally")
    plan = flow[flow.index("modal.querySelector('#game-flow-plan-later')"):flow.index("primary.addEventListener")]

    assert "transitionModal(modal, () => openNewGameModal({" in plan
    for preset in ("court: selected", "gameType: 'ranked'", "maxPlayers", "lockGameType: true", "rankedMatchMode: true"):
        assert preset in plan
    assert "carriedFromGameFlow" not in flow
    assert "Continue to date & players" not in flow

    planner = section("async function openNewGameModal", "async function renderTournaments")
    assert "const plannerFeeds = await Promise.allSettled([" in planner
    assert "api('/courts/favorites')" in planner
    assert "plannerFeedErrors.savedCourts" in planner
    assert "radius=30&limit=8" in planner
    assert "addPlannerCourtSuggestion(court || restoredCourt, 'Selected court');" in planner
    assert "state.presence && state.presence.checked_in" in planner
    assert "state.me && state.me.home_court_id" in planner
    assert "savedCourts.forEach" in planner
    assert "nearbyCourts.forEach" in planner
    assert planner.index("addPlannerCourtSuggestion(court || restoredCourt") < planner.index("savedCourts.forEach")
    load = planner[planner.index("// Gather friends"):planner.index("// Invitees from a completed game")]
    assert "if (!court) {" not in load


def test_planner_keeps_one_direct_setup_visible_for_every_entry_point():
    planner = section("async function openNewGameModal", "async function renderTournaments")

    setup_at = planner.index('<section class="planner-game-setup"')
    advanced_at = planner.index('<details class="planner-advanced')
    assert setup_at < advanced_at
    assert "const plannerSetupTitle = crewId || sessionMode ? 'Play session setup'" in planner
    assert "id: 'ng-type', name: 'ng-type', legend: 'Game type'" in planner
    assert "gameCapacityChoicesHtml('ng', presetMaxPlayers)" in planner
    assert '<input type="hidden" id="ng-max" value="${presetMaxPlayers}" />' in planner
    assert '<select id="ng-max"' not in planner
    assert 'input[name="ng-type"]' in planner
    assert 'input[name="ng-capacity"]' in planner
    assert "modal.querySelector('#ng-type').addEventListener('change'" in planner
    assert "modal.querySelector('#ng-capacity').addEventListener('change'" in planner
    assert "carriedFromGameFlow" not in planner
    assert 'class="planner-carried-setup" id="ng-carried-setup"' in planner
    assert 'id="ng-change-setup" aria-expanded="false"' in planner
    assert '<div class="planner-game-setup-head">' in planner
    assert planner.count("${plannerSetupControlsHtml}") == 1

    capacity = section("function gameCapacityChoicesHtml", "function openGameFlow")
    assert '<fieldset class="game-choice-field" id="${prefix}-capacity">' in capacity
    assert 'input type="radio" name="${prefix}-capacity"' in capacity
    assert ".game-capacity-grid" in STYLES
    assert ".game-choice-option > input:checked + .game-capacity-card" in STYLES
    assert ".planner-game-setup.is-carried" not in STYLES
    assert ".planner-carried-setup" in STYLES


def test_resumed_planner_reviews_saved_answers_before_focusing_errors():
    planner = section("async function openNewGameModal", "async function renderTournaments")

    assert "let plannerStep = restoredDraft ? 'where' : (court ? 'when' : 'where');" in planner
    assert "target.closest('#ng-step-where, #ng-step-when, #ng-step-who')" in planner
    assert "plannerStep = targetPlannerStep;" in planner
    assert "syncPlannerStep();" in planner


def test_play_launcher_is_intent_specific_while_map_and_court_keep_compatibility_flow():
    launcher = section("function rallyLauncherHtml", "async function renderPlay")
    assert 'data-goto="instant-rally"' in launcher
    assert 'data-goto="on-my-way"' in launcher
    assert 'data-goto="play-pulse"' in launcher
    assert 'data-goto="new-game"' in launcher
    assert 'data-goto="ranked-match"' in launcher
    assert "I’m at a court" in launcher
    assert "I’m free this hour" in launcher

    ctas = section("function setupEmptyStateCtas()", "// ---------- Map / Courts ----------")
    assert "target === 'game-flow'" in ctas
    assert "openPlaySoonFlow();" in ctas
    assert "target === 'new-game'" in ctas
    assert "openNewGameModal({" in ctas
    assert "target === 'ranked-match' || target === 'new-ranked-game'" in ctas
    assert "openRankedMatchFlow();" in ctas

    preview = section("function selectCourtOnMap", "function autoCheckInStorageKey")
    assert "data-preview-play>Play options</button>" in preview
    assert "openCourtPlayMenu(court);" in preview
    assert "startInstantRally" not in preview

    detail = section("async function openCourtDetail", "function openCheckInSheet")
    assert 'id="cd-play-now">${uiIcon(\'users\')} Find people to play here</button>' in detail
    assert "openCheckInSheet(court" in detail
    assert "commitLookingIntent(event.currentTarget, true);" in detail
    assert "applyAuthoritativeCheckIn(court, response, desiredLooking);" in detail
    assert "court, gameType: 'casual', maxPlayers: 6, lockGameType: true, sessionMode: true" in detail
    assert "court, mode: 'start', gameType: 'ranked', community: false" in detail
    detail_play_handler = detail[
        detail.index("modal.querySelector('#cd-play-now')"):
        detail.index("modal.querySelector('#cd-open-game')")
    ]
    assert "startInstantRally" not in detail_play_handler
