"""Focused contracts for the Play and game-detail visual language."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(start: str, end: str, source: str = APP) -> str:
    begin = source.index(start)
    return source[begin : source.index(end, begin)]


def test_rally_and_game_cards_use_the_shared_product_icon_sprite():
    assembly = section("function instantRallyAssembly", "function instantRallyScorePending")
    for icon in ("zap", "pickleball", "map-pin"):
        assert f"uiIcon('{icon}')" in assembly
    for old in ("icon: '⚡'", "icon: '🏓'", "icon: '🚗'"):
        assert old not in assembly

    cards = section("function gameCardHtml", "function bindGameButtons")
    for icon in (
        "zap",
        "trophy",
        "lock",
        "users",
        "refresh",
        "building",
        "message",
        "sliders",
        "target",
        "clock",
        "edit",
        "activity",
        "check-circle",
    ):
        assert f"uiIcon('{icon}')" in cards
    for old_markup in (
        ">⚡ ${isRankedMatch",
        ">🏆 Ranked match",
        ">🔒 Invite",
        ">🤝 Friends",
        ">🔁 Weekly",
        ">💬 ${game.chat_unread",
        ">⏳ Waitlist",
        ">✓ Confirm",
    ):
        assert old_markup not in cards


def test_game_detail_status_and_primary_actions_use_product_icons():
    detail = section("function gameScreenHtml", "async function openGameScreen")
    assert "let statusIcon = uiIcon('pickleball');" in detail
    assert 'data-status="${esc(game.status)}"' in detail
    assert 'class="game-detail-status-icon" aria-hidden="true">${statusIcon}' in detail
    assert 'class="game-detail-headline">${headline}' in detail
    assert "${uiIcon('check-circle')} We finished — wrap up session" in detail
    assert "${uiIcon('edit')} We finished — enter score" in detail
    assert "${uiIcon('check-circle')} Wrap up session" in detail
    assert "${uiIcon('edit')} Enter the score" in detail
    for action in (
        "${uiIcon('clock')} Join waitlist",
        "${uiIcon('clock')} Reschedule",
        "${uiIcon('calendar')} Add to calendar",
        "${uiIcon('calendar')} Play again",
        "${uiIcon('message')} Message the group",
    ):
        assert action in detail
    for old_markup in (
        ">📝 Enter the score",
        ">⏳ Join waitlist",
        ">🕑 Reschedule",
        ">👥 Open play group",
        ">⚡ Play again now",
    ):
        assert old_markup not in detail

    hydration = section("async function openGameScreen", "function safeNotificationOverlayRoute")
    assert "crewTarget.innerHTML = completedCrewConnectionsHtml(response.items || [])" in hydration
    assert "bindUserButtons(crewTarget)" in hydration
    assert "crewTarget.querySelectorAll('[data-connect-crew]')" in hydration

    play = section("async function renderPlay", "function updatePlayHeader")
    assert "${uiIcon(toConfirm.includes(next) ? 'activity' : toScore.includes(next) ? 'check-circle' : 'calendar')}" in play
    assert "Next up · Confirm the score" in play
    assert "Next up · Finish recent play" in play
    assert "Next up · Waiting on opponents" in play
    assert '<div class="section-label">⚡ Confirm the score</div>' not in play
    assert '<div class="section-label">⏳ Waiting on opponents</div>' not in play


def test_checkin_roster_score_and_cancel_surfaces_share_the_same_icon_language():
    checkin = section("function openCheckInSheet", "// ---------- Games ----------")
    assert 'class="checkin-sheet-icon" aria-hidden="true">${uiIcon(\'map-pin\')}' in checkin
    assert "${uiIcon('users')}" in checkin
    assert "${uiIcon('shield')}" in checkin
    assert "${uiIcon('check-circle')} Check in" in checkin
    assert "${uiIcon('eye')}" in checkin
    assert "📍" not in checkin
    assert "👍" not in checkin
    assert "👀" not in checkin

    roster = section("function openRosterBoostSheet", "function crewSummaryFrom")
    for icon in ("users", "message", "send", "check-circle"):
        assert f"uiIcon('{icon}')" in roster
    for old in (">👥</span>", ">📣</span>", ">📤</span>"):
        assert old not in roster

    score = section("function openScoreModal", "// ---------- Tournaments ----------")
    assert "${uiIcon('sliders')} Balance by rating" in score
    assert "${uiIcon('alert-triangle')} <span>Uneven teams" in score
    assert "uneven.style.display = lopsided ? 'flex' : 'none';" in score

    cancel = section(
        "function openGameCancellationConfirmation",
        "// Why a joinable game suits this player",
    )
    for icon in ("config.icon", "'map-pin'", "'alert-triangle'", "'check-circle'"):
        assert f"uiIcon({icon})" in cancel
    assert '<span aria-hidden="true">📍</span>' not in cancel
    assert '<span class="game-cancel-success-icon" aria-hidden="true">✓</span>' not in cancel


def test_play_game_icons_have_stable_geometry_and_actions_keep_large_hits():
    for selector in (
        ".checkin-sheet-icon .ui-icon",
        ".game-cancel-icon .ui-icon",
        ".status-banner .ui-icon",
        ".game-detail-status-icon .ui-icon",
        ".roster-boost-channel-icon .ui-icon",
        ".score-balance-action .ui-icon",
    ):
        assert selector in STYLES
    assert ".game-detail-title {" in STYLES
    assert ".game-action-state {" in STYLES
    assert ".score-balance-action {" in STYLES
    assert "min-height: 44px" in section(
        ".score-balance-action {", ".score-balance-action .ui-icon", STYLES
    )
    assert ".game-more-actions > summary" in STYLES
    assert ".game-danger-actions > summary" in STYLES


def test_ranked_empty_planner_group_and_mvp_choices_have_semantic_icons_and_state():
    planner = section("async function openNewGameModal", "async function renderTournaments")
    detail = section("function gameScreenHtml", "async function openGameScreen")

    assert APP.count("${uiIcon('trophy')} Start a ranked match") == 2
    assert "⚔️ Start a ranked match" not in APP
    assert planner.count("${uiIcon('users')} Starts with") >= 2
    assert "👥 Starts with" not in planner
    assert "\"You're here\"" in planner
    assert "addPlannerCourtSuggestion(item, 'Saved')" in planner
    for platform_tag in ("📍 You're here", "🏠 Home", "⭐ Saved"):
        assert platform_tag not in planner
    assert 'class="mvp-vote-options"' in detail
    assert 'aria-pressed="${game.my_mvp_vote === p.user_id}"' in detail
    assert "${uiIcon('star')}<span>${esc(p.display_name.split(' ')[0])}" in detail
    assert ".mvp-vote-options .ui-icon" in STYLES


def test_casual_play_entry_uses_scannable_product_action_rows():
    assert 'class="play-intent-choice is-primary" data-play-soon-choice="at-court"' in APP
    assert 'class="play-intent-choice" data-play-soon-choice="arriving"' in APP
    assert 'class="play-intent-choice" data-play-soon-choice="available"' in APP
    assert 'class="play-intent-choice" data-play-soon-choice="plan-group"' in APP
    assert "Check in, then join or start casual play" in APP
    assert "Choose a pickup game and share your ETA" in APP
    assert "Pick a court and let nearby players respond" in APP
    assert "Choose a time, format, and who can join" in APP
    assert ".play-intent-choice" in STYLES
    assert "min-height: 66px" in STYLES


def test_core_play_sheets_use_product_icons_in_their_titles():
    assert "const modalHead = (title, icon = '')" in APP
    assert "modalHead('Find a ranked match', 'trophy')" in APP
    assert "modalHead('Find people to play', 'users')" in APP
    assert "modalHead('Log a past game', 'edit')" in APP
    assert "modalHead(`On my way to ${rally.courtName}`, 'map-pin')" in APP
    assert "modalHead(`On your way to ${arrival.courtName}`, 'map-pin')" in APP
    assert "async function confirmArrivalAtCourtAndJoin" in APP
    assert ".modal-title-with-icon" in STYLES
    assert ".modal-title-icon .ui-icon" in STYLES


def test_play_action_completion_states_do_not_fall_back_to_font_checkmarks():
    assert "button.innerHTML = `${uiIcon('check')} Sent`;" in APP
    assert "b.innerHTML = `${uiIcon('check')} Joined · Open ${esc(playNoun)}`;" in APP
    assert "showJoinedToast(gameId" in APP
    assert "b.innerHTML = `${uiIcon('check')} Left waitlist`;" in APP
    assert 'class="arrival-status-icon" aria-hidden="true">${uiIcon(\'check\')}' in APP
    assert 'class="postgame-connected-icon" aria-hidden="true">${uiIcon(\'check-circle\')}' in APP
    assert "postButton.innerHTML = `${uiIcon('check-circle')}" in APP
    for old in ("Sent ✓", "Joined ✓ · Undo", "Joined · Undo", "Left waitlist ✓", ">✓</span>"):
        assert old not in APP
    assert ".arrival-status-icon .ui-icon" in STYLES
    assert ".postgame-connected-icon .ui-icon" in STYLES


def test_rankings_empty_states_use_shared_visuals_without_leaking_rating_history_to_profile():
    assert APP.count('class="empty-state rankings-empty"') == 2
    assert APP.count('class="empty-state-icon is-ranked"') == 2
    assert '<span class="big">🏆</span>' not in APP
    assert ".rankings-empty" in STYLES
    assert 'class="rating-history-label"' not in APP
    assert ".rating-history-label" not in STYLES


def test_every_play_flow_directions_action_signals_that_it_opens_maps():
    for class_name in ("play-pulse-directions", "arrival-directions", "gs-directions"):
        assert f'class="btn btn-secondary btn-block {class_name}' in APP
    assert APP.count("${uiIcon('external')}<span>Directions</span>") >= 5
    assert ".arrival-directions .ui-icon, .play-pulse-directions .ui-icon, .gs-directions .ui-icon" in STYLES
