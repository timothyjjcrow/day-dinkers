"""Focused contracts for Community, Me, and Competition simplification."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
INDEX = (ROOT / "public" / "index.html").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(start: str, end: str) -> str:
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_community_keeps_two_stable_lanes_with_explicit_chat_filters():
    setup = section("function configureCommunityLaneTabs", "function chatMessageActionHtml")
    for tab_id, label in (
        ("#chat-tab-chats", "Chats"),
        ("#chat-tab-friends", "People"),
    ):
        assert tab_id in setup
        assert f"'{label}'" in setup
    assert "#chat-tab-nearby" not in setup

    inbox = section("function universalInboxHtml", "function bindCommunityConversationRows")
    for value in ("all", "direct", "games", "groups", "courts"):
        assert f"['{value}'" in inbox or f"{value}:" in inbox
    assert 'class="chat-kind-filters"' in inbox
    assert "Recent chats" in inbox
    assert "Active play chats" not in inbox
    assert "inbox-row-pinned" not in inbox
    assert "Private groups" in inbox
    assert "Your community groups" in inbox
    assert "community-lane-empty" in inbox
    assert "item.kind !== 'game'" in inbox
    assert "activeStatuses.has(item.status) && item.lastMessage" in inbox
    segmented = next(block for block in STYLES.split("}") if ".segmented button" in block)
    assert "display: inline-flex" in segmented
    assert "white-space: nowrap" in segmented
    assert "min-width: 0" in segmented


def test_community_lanes_keep_partial_results_and_offer_one_retry():
    community = section("function communityPartialLoadHtml", "function nearbyPlayerLocationHtml")

    assert "function communityPartialLoadHtml(failedLabels)" in community
    assert 'role="status"' in community
    assert "data-community-feed-retry" in community
    assert "function bindCommunityPartialRetry(el)" in community
    assert "const inbox = await api('/inbox');" in community
    assert "Object.keys(inbox.errors || {})" in community
    assert "communityPartialLoadHtml(failedLabels)" in community
    assert "Promise.allSettled([" not in community
    assert "renderChat();" in community


def test_community_unread_badges_route_attention_to_the_correct_lane():
    assert 'id="chat-inbox-badge"' in INDEX
    assert 'id="chat-groups-badge"' not in INDEX

    sync = section("function syncCommunityUnreadLanes", "function renderBadges")
    assert "item.kind === 'game'" in sync
    assert "item.kind === 'tournament' || item.kind === 'league'" in sync
    assert "unreadTotal(rooms.items)" in sync
    assert "unreadTotal(clubs.items)" in sync
    assert "unreadTotal(crews.items)" in sync
    assert APP.count("syncCommunityUnreadLanes(rooms, clubs, competitions, crews);") == 1

    badges = section("function renderBadges", "async function refreshMe")
    assert "state.unreadMessages + state.communityRoomUnread" in badges
    assert "const pendingCrewInviteCount = Number(state.pendingCrewInvites?.count) || 0;" in badges
    assert "const requestTotal = state.pendingRequests + pendingCrewInviteCount;" in badges
    assert "$('#chat-groups-badge')" not in badges
    assert "`Chats, ${messagesTotal} unread message" in badges
    assert "`Community, ${communityParts.join(', ')}`" in badges
    assert "`Play, ${state.gamesToConfirm} game${state.gamesToConfirm === 1 ? '' : 's'} awaiting score confirmation`" in badges
    assert "`Activity and notifications, ${unread} unread notification${unread === 1 ? '' : 's'}`" in badges
    assert "$('#play-activity')?.setAttribute('aria-label', activityLabel);" in badges

    assert "data.community_message_unread != null" in APP
    assert "data.community_group_unread != null" in APP
    assert "data.pending_crew_invites" in APP


def test_inbox_accessible_names_include_the_visible_preview_and_time():
    inbox = section("function inboxMessagePreviewText", "function bindCommunityConversationRows")

    assert "function inboxMessagePreviewText(item)" in inbox
    assert "const preview = inboxMessagePreviewText(item);" in inbox
    assert "const when = item.lastMessage ? `, ${fmtInboxTimestamp(item.lastMessage.created_at)}` : '';" in inbox
    assert "${item.title}, ${kindLabel[item.kind]} chat${attention}, ${preview}${when}" in inbox
    assert "return esc(inboxMessagePreviewText(item));" in inbox


def test_people_rows_use_disclosed_filters_and_one_contextual_action():
    nearby = section("async function renderNearbyPlayers", "async function renderFriends")
    assert 'class="nearby-level-filters"' in nearby
    assert 'data-nearby-level="${value}"' in nearby
    assert "location_source === 'recent'" in APP
    assert "Profile area" in APP
    assert "intended destination, not current presence" not in nearby
    assert 'p.can_message ? `<button type="button" class="btn btn-secondary btn-sm" data-msg="${p.id}">' in nearby

    friends = section("async function renderFriends", "async function openThread")
    assert 'data-coming="${f.id}"' in friends
    assert 'data-invite="${f.id}"' in friends
    assert 'aria-label="Invite ${esc(f.display_name)} to a game"' in friends
    assert 'aria-haspopup="menu" aria-expanded="false"' in friends
    assert 'class="friend-row-menu" role="menu"' in friends
    assert 'bindDisclosureMenus(el)' in friends
    disclosure = section('function bindDisclosureMenus', 'async function renderFriends')
    assert "event.key === 'Escape'" in disclosure
    assert "event.key === 'ArrowDown'" in disclosure
    assert "event.key === 'ArrowUp'" in disclosure
    thread = section('async function openThread', 'function courtOpenCallFingerprint')
    assert 'aria-label="Conversation actions" aria-haspopup="menu" aria-expanded="false"' in thread
    assert 'class="thread-more-menu" role="menu"' in thread
    assert 'bindDisclosureMenus(modal)' in thread
    assert 'id="people-tab-friends"' in APP
    assert 'id="people-tab-nearby"' in APP
    assert 'aria-controls="people-content"' in APP
    assert "setupTablistKeyboard(el.querySelector('#people-mode'))" in APP

    profile = section("async function openUserProfile", "async function subscribeGamesCalendar")
    assert 'class="profile-more-actions"' in profile
    assert 'aria-haspopup="menu" aria-expanded="false"' in profile
    assert 'role="menu"' in profile
    assert "event.key === 'Escape'" in profile
    for destructive_action in ("up-remove", "up-block", "up-report"):
        assert f'id="{destructive_action}"' in profile


def test_discovery_cards_and_saved_courts_are_keyboard_pressable():
    assert "makePressable(card, () => openLeagueScreen" in APP
    assert "makePressable(card, () => openTournamentScreen" in APP
    assert 'class="row-main club-thread-head-target" id="club-head"' in APP
    assert 'class="card row nav-row-button" id="club-court"' in APP
    assert 'class="card row nav-row-button community-search-result" data-open-club=' in APP
    assert "makePressable(row, () => openCourtDetail(Number(row.dataset.pfcourt)))" in APP


def test_rankings_keep_the_viewers_position_below_the_visible_top_ten():
    rankings = section("if (seg === 'scores')", "// --- Games:")
    assert "const meIndex = me ? board.items.findIndex" in rankings
    assert "if (me && meIndex >= 10)" in rankings
    assert "rankRowHtml(boardMe, meIndex + 1, { highlight: true, name: 'You' })" in rankings
    assert 'class="rankings-more"' in rankings


def test_every_chat_uses_an_inert_bubble_and_one_native_action_button():
    assert APP.count('class="chat-message-row ${mine ? \'is-mine\' : \'is-theirs\'}"') == 7
    assert APP.count("chatMessageActionHtml(m, mine)") == 6
    assert "chatMessageActionHtml(message, mine)" in APP
    helper = section("function chatMessageActionHtml", "function setupChat")
    assert 'type="button" class="chat-message-action"' in helper
    assert 'data-message-action="delete"' in helper
    assert 'data-message-action="heart"' in helper
    assert "uiIcon('trash')" in helper
    assert "uiIcon('heart')" in helper
    assert '>🗑</button>' not in helper
    assert '>♡</button>' not in helper
    assert 'role="button"' not in helper
    for hidden_bubble_action in ("data-del-msg", "data-room-heart", "data-heart-msg"):
        assert hidden_bubble_action not in APP


def test_symbol_only_actions_have_accessible_names():
    assert APP.count('aria-label="Back">${uiIcon(\'arrow-left\')}</button>') >= 7
    assert APP.count('aria-label="Send">${uiIcon(\'send\')}</button>') >= 7
    assert 'aria-label="Back">‹</button>' not in APP
    assert 'aria-label="Send">➤</button>' not in APP
    assert 'aria-label="${i} star${i === 1 ? \'\' : \'s\'}"' in APP
    assert 'aria-label="Dispute this score">${uiIcon(\'x\')}</button>' in APP
    assert '>✕</button>' not in APP
    for label in (
        "Decrease your score",
        "Increase your score",
        "Decrease opponent score",
        "Increase opponent score",
    ):
        assert f'aria-label="{label}"' in APP
    assert 'aria-label="Decline friend request from ${esc(f.display_name)}"' in APP
    assert '${uiIcon(\'x\')} Decline</button>' in APP


def test_me_is_a_dashboard_with_five_settings_destinations():
    assert "$('#profile-settings')?.addEventListener('click', openSettingsHub);" in APP
    profile = section("async function renderProfile", "function openEditProfile")
    assert 'class="profile-dashboard-actions profile-dashboard-primary-actions"' in profile
    assert '<section class="profile-dashboard-more profile-dashboard-more-visible"' in profile
    assert "More stats and history" not in profile
    assert 'id="pf-upcoming"' in profile
    assert 'id="pf-checkout"' not in profile
    assert "const featuredCourts = rows.slice(0, 3);" in profile
    assert 'class="profile-saved-courts-more"' in profile
    assert 'id="pf-dashboard-error" role="alert"' in profile
    assert "const dashboardFailed = [mineResult, statsResult, favoritesResult, historyResult]" in profile
    assert ".every((result) => result.status === 'rejected')" in profile
    assert "el.querySelector('#pf-dashboard-error').classList.remove('hidden');" in profile
    assert 'id="pf-dashboard-retry"' in profile
    assert "renderProfile();" in profile
    for heading in ("Next session or match", "Saved courts", "Recent play", "Your play stats"):
        assert f'<div class="section-label">{heading}</div><div class="profile-section-unavailable">' in profile

    settings = section("function openSettingsHub", "async function renderProfile")
    for label in (
        "Edit profile",
        "Notifications",
        "Privacy & safety",
        "Appearance",
        "Play calendar",
        "Replay full setup",
        "Account",
    ):
        assert f"'{label}'" in settings
    assert settings.count("data-settings-destination=") == 1
    assert 'id="profile-settings"' in INDEX


def test_edit_profile_court_search_is_race_safe_and_announces_every_state():
    editor = section("function openEditProfile", "function gameFingerprint")
    assert 'id="ep-court-results" aria-live="polite"' in editor
    assert "let courtSearchSeq = 0;" in editor
    assert "const seq = ++courtSearchSeq;" in editor
    assert "if (q.length < 2)" in editor
    assert "courtResults.innerHTML = '';" in editor
    assert "courtResults.setAttribute('aria-busy', 'true');" in editor
    assert "Searching courts…" in editor
    assert "seq !== courtSearchSeq || courtSearch.value.trim() !== q" in editor
    assert "No courts found for “${esc(q)}”." in editor
    assert "Couldn’t search courts. Check the connection and try again." in editor
    assert "clearTimeout(timer);" in editor
    assert "courtSearchSeq += 1;" in editor
    assert 'value="${esc(me.home_court_name || \'\')}"' in editor
    assert "let selectedCourtName = courtSearch.value.trim();" in editor
    assert "if (q !== selectedCourtName) courtIdInput.value = '';" in editor
    assert "selectedCourtName = row.dataset.name.trim();" in editor
    assert "Choose a primary court from the search results." in editor
    assert "body.home_court_id = courtId ? Number(courtId) : null;" in editor
    assert ".city-search-feedback" in STYLES
    assert ".profile-load-error" in STYLES


def test_rankings_combine_geography_and_time_with_selected_state():
    rankings = section("if (seg === 'scores')", "// --- Games: everything actionable")
    assert 'id="board-geography" role="group" aria-label="Ranking area"' in rankings
    assert 'id="board-period" role="group" aria-label="Ranking period"' in rankings
    assert 'data-scope="near"' in rankings
    assert 'data-scope="all"' in rankings
    assert 'data-period="all"' in rankings
    assert 'data-period="month"' in rankings
    assert 'aria-pressed="${scope === \'near\'}"' in rankings
    assert "if (period === 'month') params.push('period=month');" in rankings


def test_compete_has_one_create_path_and_true_detail_tabs():
    compete = section("async function renderTournaments", "// ---------- Shared competition results")
    assert compete.count('id="competition-create"') == 1
    assert 'id="tour-create"' not in compete
    assert 'id="league-create"' not in compete

    tabs = section("function competitionDetailTabsHtml", "async function renderTournaments")
    assert 'role="tablist"' in tabs
    assert 'role="tab"' in tabs
    assert "panel.setAttribute('role', 'tabpanel')" in tabs
    assert 'aria-selected="${index === 0}"' in tabs
    assert "event.key === 'ArrowRight'" in tabs
    assert "event.key === 'ArrowLeft'" in tabs
    assert ").hidden = !active" in tabs
    assert "scrollIntoView" not in tabs

    league = section("async function openLeagueScreen", "async function openCreateTournamentSheet")
    assert "['lg-overview', 'Overview']" in league
    assert "['lg-matches', 'Matches']" in league
    assert "['lg-standings', 'Standings']" in league
    assert "['lg-chat', 'Chat']" not in league
    assert 'id="lg-chat"' in league
    assert "openChildModal(box, () => openLeagueChat(lg))" in league

    tournament = section("async function openTournamentScreen", "function openEditTournamentSheet")
    assert "['td-overview', 'Overview']" in tournament
    assert "'Bracket' : 'Matches'" in tournament
    assert "['td-chat', 'Chat']" not in tournament
    assert 'id="td-chat"' in tournament
    assert "openChildModal(box, () => openTournamentChat(t))" in tournament
    assert "snapshot?.activeCompetitionTab" in tournament


def test_competition_setup_uses_shared_product_icons_for_functional_choices():
    create = section("async function openCreateTournamentSheet", "async function openTournamentScreen")
    for icon in ("uiIcon('trophy')", "uiIcon('refresh')", "uiIcon('zap')", "uiIcon('building')"):
        assert icon in create
    for platform_glyph in ("🗂", "🔁", "⚡", "🏛"):
        assert platform_glyph not in create
    assert "tag: 'Saved'" in create
    assert "⭐ Saved" not in create
    assert ".segmented button .ui-icon" in STYLES


def test_competition_statuses_and_detail_actions_use_app_native_presentation():
    competition = section("// ---------- Tournaments ----------", "// ---------- Chat & Friends ----------")

    assert "function competitionStatusTag(label, icon, tone = '')" in competition
    assert 'aria-label="Competition status: ${esc(label)}"' in competition
    for icon in ("ticket", "activity", "trophy", "alert-triangle", "building", "shield", "check-circle"):
        assert f"uiIcon('{icon}')" in competition or f"'{icon}'," in competition
    for old_glyph in ("📦", "👑", "🙌", "🏁", "⏭", "🥉", "▶️", "🙋", "⚡", "🏛", "📣", "👋", "⏳"):
        assert old_glyph not in competition

    assert 'class="competition-nav-tags">${statusTag}${joinedTag}' in competition
    assert 'type="button" class="btn btn-secondary btn-block competition-chat-action" id="lg-chat"' in competition
    assert competition.count('class="competition-chat-image-loading"') == 2
    assert competition.count('data-img-id="${m.id}" aria-hidden="true"') >= 2
    assert 'data-select-title="Players per box" data-select-prefix="Box size"' in competition
    assert competition.count('data-select-title="Tournament field size" data-select-prefix="Players or teams"') == 2

    for selector in (
        ".competition-status-tag .ui-icon",
        ".competition-champion-card",
        ".competition-chat-action",
        ".competition-chat-image-loading",
        ".competition-organizer-actions .btn",
    ):
        assert selector in STYLES


def test_pinned_competition_action_covers_global_eligible_ctas():
    actions = section("function tournamentCheckinState", "function captureCompetitionViewState")
    assert "function competitionActionNeeded(kind, parent)" in actions
    assert "kind === 'league' && parent.status === 'registration'" in actions
    assert "parent.is_organizer && Number(parent.member_count) >= 3" in actions
    assert "!parent.joined && Number(parent.member_count) < Number(parent.max_players)" in actions
    assert "action: 'join'" in actions
    assert "action: 'start'" in actions
    assert "kind === 'tournament'" in actions
    assert "const { canCheckIn } = tournamentCheckinState(parent);" in actions
    assert "!parent.my_entry_id" in actions
    assert "Number(parent.entry_count) < Number(parent.max_entries)" in actions
    assert "action: 'checkin'" in actions
    assert "action: 'register'" in actions
    assert "direct: !needsPartner" in actions
    assert 'data-competition-global-action="${next.action}"' in actions
    assert "function bindCompetitionGlobalAction(root)" in actions

    league = section("async function openLeagueScreen", "async function openCreateTournamentSheet")
    tournament = section("async function openTournamentScreen", "function openEditTournamentSheet")
    assert league.index("${nextActionHtml}") < league.index("${competitionDetailTabsHtml(leagueNav)}")
    assert tournament.index("${nextActionHtml}") < tournament.index("${competitionDetailTabsHtml(tournamentNav)}")
    assert "bindCompetitionGlobalAction(content);" in league
    assert "bindCompetitionGlobalAction(content);" in tournament


def test_league_matches_and_standings_have_strict_panel_boundaries():
    league = section("async function openLeagueScreen", "async function openCreateTournamentSheet")
    active_start = league.index("const myId = state.me.id;")
    active_end = league.index("content.innerHTML = body;", active_start)
    active = league[active_start:active_end]
    matches_boundary = active.index('id="lg-matches"')
    standings_boundary = active.index('id="lg-standings"')
    matches_panel_source = active[matches_boundary:standings_boundary]
    standings_panel_source = active[standings_boundary:]

    assert "matchesByBox" in matches_panel_source
    assert "leagueMatchCardHtml(match" in matches_panel_source
    assert "competition-organizer-actions" in matches_panel_source
    assert "leagueMatchCardHtml" not in standings_panel_source
    assert "competition-organizer-actions" not in standings_panel_source
    assert "boxes[boxNumber].sort(rankMember)" in standings_panel_source


def test_crew_planner_keeps_the_group_and_offers_clear_session_visibility():
    planner = section("async function openNewGameModal", "async function renderTournaments")
    assert 'id="ng-step-where"' in planner
    assert 'id="ng-step-when"' in planner
    assert 'id="ng-step-who" aria-labelledby="planner-who-title"' in planner
    assert 'id="ng-crew-private" role="status"' in planner
    assert "const finalStep = plannerStep === 'who'" in planner
    assert '<b>Group only</b>' in planner
    assert '<b>Friends</b><small>Selected group + friends</small>' in planner
    assert '<b>Nearby players</b><small>Open at the court</small>' in planner
    assert "Accepted members start selected. Deselect anyone" in planner
    assert "const maximum = crewId ? 12 : CASUAL_GAME_MAX_PLAYERS" in planner
    assert "const minimum = crewId ? Math.max(6, inviteIds.size + 1) : 6" in planner
    assert "Selected group players are included; friends can fill the extra spots." in planner
    assert "Accepted Crew snapshot" not in planner


def test_ranked_and_casual_copy_matches_the_selected_state():
    create = section("function openCreateTournamentSheet", "function openTournamentScreen")
    assert "Casual: results are recorded, with no rating changes." in create
    assert "Ranked: every match counts toward player ratings when the tournament finishes." in create
    assert "modal.querySelector('#tc-ranked-hint').textContent = value" in create


def test_competition_details_acknowledge_taps_before_network_and_retry_in_place():
    league = section("async function openLeagueScreen", "async function openCreateTournamentSheet")
    tournament = section("async function openTournamentScreen", "function openEditTournamentSheet")
    for source, endpoint in (
        (league, "api(detailPath, { responseMeta: true })"),
        (tournament, "api(detailPath, { responseMeta: true })"),
    ):
        assert "openDetailLoadShell({" in source
        assert source.index("openDetailLoadShell({") < source.index(endpoint)
        assert "renderDetailLoadError(" in source
        assert "retryDetailLoad(shell" in source
        assert "removeAttribute('aria-busy')" in source


def test_competition_creation_acknowledges_taps_before_optional_setup_requests():
    league = section("async function openCreateLeagueSheet", "async function openCreateTournamentSheet")
    tournament = section("async function openCreateTournamentSheet", "function tournamentTitlesHtml")

    for source, endpoint in (
        (league, "api('/clubs/mine')"),
        (tournament, "api('/courts/favorites')"),
    ):
        assert "openDetailLoadShell({" in source
        assert source.index("openDetailLoadShell({") < source.index(endpoint)
        assert "hydrateDetailLoadShell(shell" in source
        assert "!shell.modal.isConnected" in source
        assert "return modal;" in source

    assert 'role="combobox" aria-autocomplete="list"' in tournament
    assert 'role="listbox" aria-live="polite"' in tournament
    assert "Searching courts…" in tournament
    assert "tournamentCourtSearchSeq" in tournament
    assert "data-court-search-retry" in tournament


def test_irreversible_league_membership_actions_use_designed_confirmations():
    league = section("async function openLeagueScreen", "async function openCreateTournamentSheet")
    start = league[league.index("content.querySelector('#lg-start')"):]
    assert "act('start', {" in start
    assert "Start this league now?" in start
    assert "Keep signups open" in start
    assert "Start league" in start

    leave = league[league.index("content.querySelector('#lg-leave')"):]
    assert "act('leave', {" in leave
    assert "Leave this league?" in leave
    assert "Leave league" in leave
    assert "Stay in league" in leave


def test_match_results_and_tournament_editors_are_true_child_steps():
    competition = section("async function openLeagueScreen", "async function openTournamentChat")
    assert "openChildModal(box, () => openCompetitionResultSheet('league'" in competition
    assert "openChildModal(box, () => openCompetitionResultSheet('tournament'" in competition
    assert "openChildModal(box, () => openEditTournamentSheet(t, render))" in competition
    editor = section("function openEditTournamentSheet", "async function openTournamentChat")
    assert "return modal;" in editor


def test_game_chat_uses_the_server_game_type_for_inbox_opened_rooms():
    chat = section("async function openGameChat", "// ---------- User profile")
    assert "const gameType = data.game?.game_type || game.game_type;" in chat
    assert "const playNoun = gameType === 'ranked' ? 'match' : 'play session';" in chat
    assert chat.index("await api(`/games/${game.id}/chat`)") < chat.index("const gameType =")
