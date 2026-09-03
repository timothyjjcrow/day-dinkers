"""Source contracts for the community-first Casual, Ranked, and Groups UX."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(start: str, end: str) -> str:
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_play_launcher_has_explicit_now_arrival_availability_and_planning_paths():
    launcher = section("function rallyLauncherHtml", "async function renderPlay")
    assert 'data-goto="instant-rally"' in launcher
    assert "<b>${here ? 'Play here' : 'At a court'}</b>" in launcher
    assert 'data-goto="on-my-way"' in launcher
    assert '<b>On my way</b>' in launcher
    assert 'data-goto="play-pulse"' in launcher
    assert '<b>Free this hour</b>' in launcher
    assert 'data-goto="new-game"' in launcher
    assert 'data-goto="ranked-match"' in launcher
    assert 'data-goto="game-flow"' not in launcher


def test_community_compatibility_routes_keep_court_and_never_open_mixed_shell():
    flow = section("function openGameFlow(options = {})", "async function checkInAndStartRally")
    guard = flow[flow.index("if (gameType === 'casual')"):flow.index("const lockedGameType")]
    assert "mode === 'schedule'" not in guard
    assert "openPlayNowCourtPicker({ court: options.court })" in guard
    assert "openPlaySoonFlow()" in guard
    assert "const lockedGameType = 'ranked';" in flow
    assert "Find or start a game" not in flow


def test_empty_casual_search_routes_to_court_group_presence_not_generic_start():
    flow = section("function openGameFlow(options = {})", "async function checkInAndStartRally")
    find = flow[flow.index("const loadFindGames = async () => {"):flow.index("const selectCourt = (court) => {")]
    assert 'data-game-flow-casual-group' in find
    assert 'Gather a group at this court' in find
    assert 'Find people to play' in find
    assert "openPlayNowCourtPicker({ court: selected })" in find
    assert "gameType === 'casual'" in find
    assert "data-game-flow-switch-start>Start this ranked setup" in find


def test_ranked_entry_is_match_and_opponent_specific():
    flow = section("function openGameFlow(options = {})", "async function checkInAndStartRally")
    launcher = section("function openRankedMatchFlow", "function openRankedOpponentPicker")
    opponent = section("function openRankedOpponentPicker", "function openPlaySoonFlow")
    for copy in ('Find a ranked match', 'Challenge someone you know', 'Start now', 'Plan a ranked match for later'):
        assert copy in flow
    assert "openChildModal(modal, () => openRankedOpponentPicker({" in flow
    assert "court: selected, maxPlayers, onCreated: options.onCreated" in flow
    assert "return openGameFlow({" in launcher
    assert "gameType: 'ranked', maxPlayers: 2, lockGameType: true" in launcher
    assert "openChallengeSheet(player, court, { onCreated })" in opponent


def test_ranked_challenge_keeps_the_selected_court_and_never_silently_downgrades_doubles():
    flow = section("function openGameFlow(options = {})", "async function checkInAndStartRally")
    opponent = section("function openRankedOpponentPicker", "function openPlaySoonFlow")

    assert "openRankedOpponentPicker({" in flow
    assert "court: selected, maxPlayers, onCreated: options.onCreated" in flow
    assert "if (maxPlayers !== 2)" in flow
    assert "Direct challenges are singles only" in flow
    assert "challengeButton.setAttribute('aria-disabled', String(maxPlayers !== 2));" in flow
    assert "function openRankedOpponentPicker({" in APP
    assert "court: requestedCourt = null" in opponent
    assert "onCreated = null" in opponent
    assert "playNowCourt(requestedCourt || fallbackCourt" in opponent
    assert "const challengeFormat = Number(maxPlayers) === 2 ? 2 : 4;" in opponent
    assert "court," in opponent
    assert "gameType: 'ranked'" in opponent
    assert "maxPlayers: challengeFormat" in opponent


def test_ranked_opponent_loading_is_retryable_and_ignores_stale_responses():
    opponent = section("function openRankedOpponentPicker", "function openPlaySoonFlow")

    assert "let opponentLoadSeq = 0;" in opponent
    assert "const loadOpponents = async () =>" in opponent
    assert "const requestSeq = ++opponentLoadSeq;" in opponent
    assert "requestSeq !== opponentLoadSeq" in opponent
    assert "renderError(list, error.message, loadOpponents);" in opponent
    assert "modal._cleanupFns.push(() => { opponentLoadSeq += 1; });" in opponent


def test_ranked_plan_later_stays_locked_and_defaults_to_singles_everywhere():
    flow = section("function openGameFlow(options = {})", "async function checkInAndStartRally")
    assert "requestedMaxPlayers === 2 || requestedMaxPlayers === 4" in flow
    assert "lockedGameType === 'ranked' ? 2 : 4" in flow
    plan = flow[flow.index("modal.querySelector('#game-flow-plan-later')"):
                flow.index("primary.addEventListener")]
    assert "lockGameType: true" in plan
    assert "rankedMatchMode: true" in plan
    assert "court: selected" in plan
    assert "maxPlayers" in plan
    assert "mode === 'schedule'" not in flow


def test_casual_entry_uses_presence_arrival_pulse_or_group_planning():
    ctas = section("function setupEmptyStateCtas", "// ---------- Map / Courts ----------")
    checked_in = ctas[ctas.index("target === 'casual-play'"):ctas.index("target === 'instant-rally'")]
    assert "state.presence && state.presence.checked_in" in checked_in
    assert "if (currentCourt) openPlayNowCourtPicker({ court: currentCourt });" in checked_in
    assert "else openPlaySoonFlow();" in checked_in

    casual = section("function openPlaySoonFlow", "async function openPlaySoonArrivalChoices")
    assert "Find people to play" in casual
    assert "openPlayNowCourtPicker({ court: currentCourt })" in casual
    assert "openPlaySoonArrivalChoices" in casual
    assert "openPlayPulseCourtPicker" in casual
    assert "Gather a group for later" in casual
    assert "gameType: 'casual', maxPlayers: 6, lockGameType: true, sessionMode: true" in casual
    assert "openGameFlow(" not in casual


def test_preset_court_is_confirmable_before_optional_court_search():
    picker = section("async function openPlayNowCourtPicker", "function openPlayPulseCourtPicker")
    assert picker.index('id="play-now-selection"') < picker.index('id="play-now-court-picker"')
    assert 'class="play-now-court-picker ${preset ? \'hidden\' : \'\'}"' in picker
    assert 'id="play-now-change-court"' in picker
    assert 'aria-controls="play-now-court-picker"' in picker
    assert "setPickerOpen(true, { focusSearch: true })" in picker
    assert "syncSelection({ collapsePicker: true });" in picker
    assert "confirmButton.disabled = !selected;" in picker
    assert "if (!pickerEl.classList.contains('hidden'))" in picker
    assert ".play-now-selection .btn { min-height: 44px" in STYLES


def test_saved_plans_preserve_their_session_or_ranked_lane():
    drafts = section("function safeGameDraftRecord", "function readGameDraft")
    planner = section("async function openNewGameModal", "async function renderTournaments")
    for field in ("lockGameType", "sessionMode", "rankedMatchMode"):
        assert f"{field}:" in drafts
        assert f"{field}," in planner
    assert "restoredDraft?.lockGameType === true" in planner
    assert "restoredDraft?.sessionMode === true" in planner
    assert "restoredDraft?.rankedMatchMode === true" in planner


def test_cards_call_casual_play_sessions_and_ranked_matches():
    cards = section("function gameCardHtml", "// Share text")
    assert "const playNoun = isRankedMatch ? 'match' : 'session';" in cards
    assert "'Ranked match' : Number(game.max_players) > 4 ? 'Group session' : 'Casual game'" in cards
    assert "Play session ·" in cards
    assert "Join ${playNoun}" in cards
    assert "This pickup group ended" in cards


def test_detail_chat_and_manage_actions_use_session_or_match_nouns():
    detail = section("function gameScreenHtml", "async function openGameScreen")
    assert "const playNoun = isRankedMatch ? 'match' : 'play session';" in detail
    assert "const playNounTitle = isRankedMatch ? 'Match' : 'Play session';" in detail
    for copy in (
        "Join this ${playNoun}", "Share ${playNoun}", "Leave ${playNoun}",
        "Cancel ${playNoun}", "${playNounTitle} chat",
    ):
        assert copy in detail
    cancellation = section("function gameCancellationVariant", "function openGameCancellationConfirmation")
    assert "Cancel this ${playNoun}?" in cancellation
    for stale in ("Game on!", "Game chat", "Share game", "Leave game", "Cancel this game"):
        assert stale not in detail
    screen = section("async function openGameScreen", "function safeNotificationOverlayRoute")
    assert "'Ranked match details' : 'Play session details'" in screen
    chat = section("async function openGameChat", "function mutualFriendsText")
    assert "${playNounTitle} chat" in chat
    assert "Message your ${playNoun}" in chat


def test_private_play_group_can_start_cold_with_multiple_friends_and_a_court():
    create = section("async function openCreatePlayGroupSheet", "async function openCrewInviteSheet")
    assert "Start a play group" in create
    assert "private, invite-only group" in create
    assert "api('/friends')" in create
    assert "new Set()" in create
    assert "clubCourtPicker(modal, 'pg')" in create
    assert "api('/crews'" in create
    assert "default_court_id:" in create
    assert "invite_user_ids: [...selectedIds]" in create
    assert "response.crew || response" in create
    assert "'Crew court'" not in APP


def test_private_play_group_requires_invitation_review_instead_of_false_success():
    create = section("async function openCreatePlayGroupSheet", "async function openCrewInviteSheet")
    assert "const needsInviteReview = sent !== requested || skipped.length > 0" in create
    assert "if (needsInviteReview)" in create
    assert 'role="alert" tabindex="-1" id="pg-invite-review"' in create
    assert "Review this play group before continuing" in create
    assert "could not be invited" in create
    assert 'id="pg-review-group"' in create
    review = create[create.index("if (needsInviteReview)"):create.index("toast(`Play group started")]
    assert review.index("finish();") < review.index("form.innerHTML")
    assert "toast(" not in review
    assert "transitionModal(modal, () => openCrewScreen(crew.id))" in review


def test_owner_can_add_multiple_players_with_durable_crew_invites():
    invite = section("async function openCrewInviteSheet", "function crewPlannerOptions")
    assert "crew.pending_invites" in invite
    assert "data-crew-invite-friend" in invite
    assert "api(`/crews/${crew.id}/invites`" in invite
    assert "body: JSON.stringify({ invite_user_ids: [...selectedIds] })" in invite
    assert "const needsInviteReview = sent !== requested || skipped.length > 0" in invite
    assert 'role="alert" tabindex="-1" id="crew-invite-review"' in invite
    assert "Review this play group before continuing" in invite
    assert 'id="crew-review-group"' in invite
    screen = section("async function openCrewScreen", "function openRenameCrewSheet")
    assert 'id="crew-add-players"' in screen
    assert "openCrewInviteSheet(crew, () => {" in screen
    assert "transitionModal(modal, () => openCrewScreen(crew.id));" in screen
    assert "Plan with this group" in screen


def test_group_planner_is_casual_supports_twelve_and_has_three_audiences():
    options = section("function crewPlannerOptions", "async function openCrewScreen")
    assert "game_type: 'casual'" in options
    assert "lockGameType: true" in options
    assert "sessionMode: true" in options
    assert "Math.min(12, Math.max(6, total))" in options
    planner = section("async function openNewGameModal", "async function renderTournaments")
    for label in ("Group only", "Friends", "Nearby players"):
        assert f'<b>{label}</b>' in planner
    assert "openPlayerCount.disabled = gameType === 'ranked'" in planner
    assert "const maximum = crewId ? 12 : CASUAL_GAME_MAX_PLAYERS" in planner
    assert "const minimum = crewId ? Math.max(6, inviteIds.size + 1) : 6" in planner
    assert "Ranked match setup" in planner


def test_clubs_are_presented_as_public_community_groups_with_planning():
    inbox = section("function universalInboxHtml", "function bindCommunityConversationRows")
    assert "Private groups" in inbox
    assert "Your community groups" in inbox
    assert 'id="group-new"' in inbox
    assert "Find public groups" in inbox
    create = section("function openCreateClubSheet", "function openEditClubSheet")
    assert "Start a community group" in create
    assert "public and discoverable" in create
    assert "A club is your crew" not in create
    club = section("function openClubInfo", "async function openClubInviteSheet")
    assert "Plan with this community" in club
    assert 'id="club-plan-weekly"' in club
    assert "Weekly open play" in club
    assert "clubId: club.id" in club
    assert "communityName: club.name" in club
    assert "openClubPlanner({ weekly: true })" in club
    assert "scheduledAt: weeklyStart.toISOString()" in club
    assert "recurrence: 'weekly'" in club
    assert "recurrenceWeekdays: [weeklyDay]" in club
    assert "recurrenceEndsOn: weeklyDate(weeklyEnd)" in club
    planner = section("async function openNewGameModal", "async function renderTournaments")
    assert "if (clubId) visibility = 'open'" in planner
    assert "Community sessions stay open so every member can see and join them." in planner
    assert "Public community sessions are open to every member" in planner
    assert "button.disabled = unavailable" in planner
    assert "clubId && visibility !== 'open'" in planner


def test_opening_an_inbox_room_refreshes_the_lane_only_after_return():
    rows = section("function bindCommunityConversationRows", "function openCreateGroupChoiceSheet")
    assert "let roomModal = null" in rows
    assert "roomModal._cleanupFns.push" in rows
    assert "refreshedAfterClose" in rows
    assert "queueMicrotask(() =>" in rows
    assert "state.tab === 'chat' && state.chatSeg === 'chats'" in rows
    assert "if (state.tab === 'chat') renderChat();" not in rows


def test_new_interactions_keep_phone_sized_controls_and_selected_states():
    for selector in (
        ".ranked-match-choice", ".play-group-friend", ".crew-add-players",
        ".community-start-group", ".play-empty-actions",
    ):
        assert selector in STYLES
    assert ".play-group-friend.active" in STYLES
    assert "min-height: 56px" in STYLES
