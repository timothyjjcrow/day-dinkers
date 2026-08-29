"""Focused contracts for Community, Me, and Competition simplification."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
INDEX = (ROOT / "public" / "index.html").read_text()


def section(start: str, end: str) -> str:
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_community_keeps_three_stable_lanes_and_persistent_groups():
    setup = section("function configureCommunityLaneTabs", "function chatMessageActionHtml")
    for tab_id, label in (
        ("#chat-tab-chats", "Messages"),
        ("#chat-tab-friends", "People"),
        ("#chat-tab-nearby", "Groups"),
    ):
        assert tab_id in setup
        assert f"'{label}'" in setup

    inbox = section("function universalInboxHtml", "function bindCommunityConversationRows")
    assert "lane === 'groups'" in inbox
    assert "? groupKinds.has(item.kind)" in inbox
    assert "Active game chats" in inbox
    assert "inbox-row-pinned" in inbox
    assert "Your crews" in inbox
    assert "Your clubs" in inbox
    assert "community-lane-empty" in inbox
    assert "{ items: [] }, rooms, clubs, competitions, crews, { lane: 'groups' }," in APP
    assert "item.kind !== 'game' || activeStatuses.has(item.status) || item.unread > 0" in inbox


def test_community_unread_badges_route_attention_to_the_correct_lane():
    assert 'id="chat-inbox-badge"' in INDEX
    assert 'id="chat-groups-badge"' in INDEX

    sync = section("function syncCommunityUnreadLanes", "function renderBadges")
    assert "item.kind === 'game'" in sync
    assert "item.kind === 'tournament' || item.kind === 'league'" in sync
    assert "unreadTotal(rooms.items)" in sync
    assert "unreadTotal(clubs.items)" in sync
    assert "unreadTotal(crews.items)" in sync
    assert APP.count("syncCommunityUnreadLanes(rooms, clubs, competitions, crews);") == 2

    badges = section("function renderBadges", "async function refreshMe")
    assert "state.unreadMessages + state.communityMessageUnread" in badges
    assert "const groupsTotal = state.communityGroupUnread;" in badges
    assert "state.unreadMessages + state.communityRoomUnread + state.pendingRequests" in badges
    assert "$('#chat-groups-badge')" in badges
    assert "`Groups, ${groupsTotal} unread`" in badges

    assert "data.community_message_unread != null" in APP
    assert "data.community_group_unread != null" in APP


def test_people_rows_use_disclosed_filters_and_one_contextual_action():
    nearby = section("async function renderNearbyPlayers", "async function renderFriends")
    assert '<details class="nearby-filter"' in nearby
    assert 'id="nearby-skill"' in nearby
    assert "How location sharing works" in nearby
    assert 'data-msg="${p.id}">Message</button>' in nearby

    friends = section("async function renderFriends", "async function openThread")
    assert 'data-coming="${f.id}"' in friends
    assert 'data-invite="${f.id}"' in friends
    assert 'aria-label="Invite ${esc(f.display_name)} to a game"' in friends
    assert 'id="people-tab-friends"' in APP
    assert 'id="people-tab-nearby"' in APP
    assert 'aria-controls="people-content"' in APP
    assert "setupTablistKeyboard(el.querySelector('#people-mode'))" in APP

    profile = section("async function openUserProfile", "async function subscribeGamesCalendar")
    assert '<details class="profile-more-actions">' in profile
    for destructive_action in ("up-remove", "up-block", "up-report"):
        assert f'id="{destructive_action}"' in profile


def test_discovery_cards_and_saved_courts_are_keyboard_pressable():
    assert "makePressable(card, () => openLeagueScreen" in APP
    assert "makePressable(card, () => openTournamentScreen" in APP
    assert "makePressable(modal.querySelector('#club-head')" in APP
    assert "makePressable(modal.querySelector('#club-court')" in APP
    assert "querySelectorAll('[data-open-club]').forEach((row) => makePressable" in APP
    assert "makePressable(row, () => openCourtDetail(Number(row.dataset.pfcourt)))" in APP


def test_rankings_keep_the_viewers_position_below_the_visible_top_ten():
    rankings = section("if (seg === 'scores')", "// --- Games:")
    assert "const meIndex = me ? board.items.findIndex" in rankings
    assert "if (me && meIndex >= 10)" in rankings
    assert '<div class="rank-num">${meIndex + 1}</div>' in rankings


def test_every_chat_uses_an_inert_bubble_and_one_native_action_button():
    assert APP.count('class="chat-message-row ${mine ? \'is-mine\' : \'is-theirs\'}"') == 7
    assert APP.count("chatMessageActionHtml(m, mine)") == 6
    assert "chatMessageActionHtml(message, mine)" in APP
    helper = section("function chatMessageActionHtml", "function setupChat")
    assert 'type="button" class="chat-message-action"' in helper
    assert 'data-message-action="delete"' in helper
    assert 'data-message-action="heart"' in helper
    assert 'role="button"' not in helper
    for hidden_bubble_action in ("data-del-msg", "data-room-heart", "data-heart-msg"):
        assert hidden_bubble_action not in APP


def test_symbol_only_actions_have_accessible_names():
    assert APP.count('aria-label="Back">‹</button>') >= 7
    assert 'aria-label="${i} star${i === 1 ? \'\' : \'s\'}"' in APP
    assert 'aria-label="Dispute this score">✕</button>' in APP
    for label in (
        "Decrease your score",
        "Increase your score",
        "Decrease opponent score",
        "Increase opponent score",
    ):
        assert f'aria-label="{label}"' in APP
    assert 'aria-label="Decline friend request from ${esc(f.display_name)}"' in APP
    assert 'aria-label="Remove ${esc(m.display_name)} from club"' in APP


def test_me_is_a_dashboard_with_five_settings_destinations():
    assert "$('#profile-settings')?.addEventListener('click', openSettingsHub);" in APP
    profile = section("async function renderProfile", "function openEditProfile")
    assert 'class="profile-dashboard-actions"' in profile
    assert '<details class="profile-dashboard-more">' in profile
    assert 'id="pf-upcoming"' in profile
    assert 'id="pf-checkout"' not in profile
    assert "const featuredCourts = rows.slice(0, 3);" in profile
    assert 'class="profile-saved-courts-more"' in profile

    settings = section("function openSettingsHub", "async function renderProfile")
    for label in (
        "Edit profile",
        "Notifications",
        "Privacy & safety",
        "Appearance & calendar",
        "Account",
    ):
        assert f"'{label}'" in settings
    assert settings.count("data-settings-destination=") == 1
    assert 'id="profile-settings"' in INDEX


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
    assert "['lg-chat', 'Chat']" in league

    tournament = section("async function openTournamentScreen", "function openEditTournamentSheet")
    assert "['td-overview', 'Overview']" in tournament
    assert "'Bracket' : 'Matches'" in tournament
    assert "['td-chat', 'Chat']" in tournament
    assert "snapshot?.activeCompetitionTab" in tournament


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
    active_end = league.index("if (lg.joined)", active_start)
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


def test_crew_planner_skips_visibility_and_ends_with_private_schedule_summary():
    planner = section("async function openNewGameModal", "async function renderTournaments")
    assert 'id="ng-step-where"' in planner
    assert 'id="ng-step-when"' in planner
    assert 'id="ng-step-who" aria-hidden="true"' in planner
    assert 'id="ng-crew-private" role="status"' in planner
    assert "const finalStep = crewId ? plannerStep === 'when'" in planner
    assert "visibility = crewId ? 'private'" in planner
    assert "Accepted Crew snapshot" not in planner


def test_ranked_and_casual_copy_matches_the_selected_state():
    create = section("function openCreateTournamentSheet", "function openTournamentScreen")
    assert "Casual: results are recorded, with no rating changes." in create
    assert "Ranked: every match counts toward player ratings when the tournament finishes." in create
    assert "modal.querySelector('#tc-ranked-hint').textContent = value" in create
