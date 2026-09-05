"""Focused browser contracts for the private Crew loop."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()
CSS = (ROOT / 'public' / 'styles-v15.css').read_text()


def test_completed_game_reviews_source_roster_before_optional_group_save():
    assert "crewRequest || api(`/games/${game.id}/crew`)" in APP
    assert 'id="cel-play-again"' in APP
    assert 'id="gs-play-again"' in APP
    assert 'options.offerSaveGroup = false;' in APP
    assert 'id="gs-save-group"' in APP
    assert 'sourceGame: game,' in APP
    assert 'game.saved_crew = crew;' in APP
    assert 'id="ng-save-group"' in APP
    assert "api(`/games/${sourceGameId}/crew`, {" in APP
    assert "body: JSON.stringify({ name: saveGroupName })" in APP
    assert 'id="ng-save-group-name" maxlength="80"' in APP
    assert 'if (sourceGameId && modal.querySelector(\'#ng-save-group\')?.checked)' in APP
    assert 'openCompletedCrewPlanner' not in APP


def test_crew_identity_and_version_survive_editing_and_immutable_retry():
    assert 'crew_id: positiveId(value.crew_id)' in APP
    assert 'expected_crew_version: Number.isSafeInteger(crewVersion)' in APP
    assert 'const crewId = id(raw.crewId)' in APP
    assert 'crewVersion: raw.crewVersion != null && Number.isSafeInteger(Number(raw.crewVersion))' in APP
    assert 'crewId,' in APP
    assert 'crewVersion,' in APP
    assert 'crew_id: restoredDraft.crewId' in APP
    assert 'expected_crew_version: restoredDraft.crewVersion' in APP
    assert 'crew_id: crewId' in APP
    assert 'expected_crew_version: crewVersion' in APP


def test_attached_crew_planner_selects_members_and_offers_safe_audiences():
    assert 'id="ng-crew-private"' in APP
    assert "Starts with ${esc(crewName || 'your play group')}" in APP
    for label in ('Group only', 'Friends', 'Nearby players'):
        assert f'<b>{label}</b>' in APP
    assert "${crewId ? 'Group players' : 'Direct invitations'}" in APP
    assert 'if (!btn || btn.disabled || crewId) return;' not in APP
    assert 'const plannedPlayerCount = inviteIds.size + 1;' in APP
    assert "recurrence: recurringBox.checked ? 'weekly' : 'none'" in APP
    assert '...(recurringBox.checked ? {' in APP
    assert 'const recurringAllowed = !isRanked;' in APP
    assert 'const crewMinimum = crewId ? inviteIds.size + 1 : 1;' in APP
    assert 'capacity < crewMinimum' in APP
    assert "friendsWrap.classList.remove('hidden');" in APP
    assert "if (crewId && btn.dataset.vis !== 'private') return;" not in APP


def test_stale_crew_version_refreshes_full_member_detail_before_resubmit():
    assert "if (err.code === 'crew_changed' && crewId)" in APP
    assert 'const detail = await api(`/crews/${crewId}`)' in APP
    assert 'const schedulable = applyFreshCrewRoster(detail)' in APP
    assert 'detail.members' in APP
    assert 'crewVersion = summary.roster_version' in APP
    assert "submitButton.textContent = 'Refreshing group players…'" in APP


def test_group_friend_invite_and_member_rows_use_explicit_skill_identities():
    create = APP[
        APP.index('async function openCreatePlayGroupSheet'):
        APP.index('async function openCrewInviteSheet')
    ]
    invite = APP[
        APP.index('async function openCrewInviteSheet'):
        APP.index('function crewPlannerOptions')
    ]
    detail = APP[
        APP.index('async function openCrewScreen'):
        APP.index('function openRenameCrewSheet')
    ]
    for source in (create, invite, detail):
        assert 'playerSkillIdentityHtml(' in source
        assert 'skillLabel(friend.skill_level)' not in source
        assert 'skillLabel(member.skill_level)' not in source


def test_community_has_crews_pending_invites_and_response_actions():
    assert "api('/crews/mine')" in APP
    assert "Promise.allSettled([" in APP
    assert "{ items: [], invitations: [] }" in APP
    assert "communityPartialLoadHtml(failedLabels)" in APP
    assert "kind: 'crew', id: crew.id" in APP
    assert '>Play group invitations<' in APP
    assert 'data-crew-response="accept"' in APP
    assert 'data-crew-response="decline"' in APP
    assert "api(`/crews/${crewId}/respond`" in APP
    assert 'body: JSON.stringify({ accept })' in APP
    assert "? await openCrewScreen(id) : await openCrewChatById(id)" in APP
    assert "roomModal._cleanupFns.push" in APP
    assert '>Decline</button>' in APP


def test_pending_invite_routes_open_the_consent_card_in_place():
    assert "const mine = await api('/crews/mine')" in APP
    assert 'pendingCrewInvitationEntries(mine).find' in APP
    assert 'renderCrewInvitationConsent(shell, pending.invitation)' in APP
    assert 'Private play group invitation' in APP
    assert 'Only members can see this group’s player list' in APP
    screen = APP[APP.index('async function openCrewScreen'):APP.index('function openRenameCrewSheet')]
    assert "toast('Play group invitation ready — choose Join group or Decline')" not in screen


def test_crew_home_management_chat_outbox_and_hash_route_are_wired():
    assert "api(`/crews/${crewId}`)" in APP
    assert "upcomingGames.length ? 'Plan another' : 'Plan with this group'" in APP
    assert "uiIcon('message')} Group chat" in APP
    assert "api(`/crews/${crew.id}`, { method: 'PATCH'" in APP
    assert "api(`/crews/${crew.id}/leave`, { method: 'POST' })" in APP
    assert "api(`/crews/${crew.id}`, { method: 'DELETE' })" in APP
    assert "api(`/crews/${crew.id}/chat`)" in APP
    assert 'bindChatContinuity(modal, msgsEl, input, `crew:${crew.id}`, { emptyMessageHtml })' in APP
    assert "crew: 'crews'" in APP
    assert "'court', 'game', 'tournament', 'club', 'crew', 'league'" in APP
    assert "else if (route.kind === 'crew') openCrewScreen(route.id);" in APP
    assert "prepareRoute('crew', id); openCrewScreen(id);" in APP
    assert "await purgeChatOutboxChannel(state.me?.id, `crew:${crew.id}`);" in APP
    assert "item.channelKey.startsWith('crew:')" in APP


def test_crew_chat_info_target_respects_the_actual_retained_parent():
    screen_start = APP.index("async function openCrewScreen")
    screen_end = APP.index("function openRenameCrewSheet", screen_start)
    screen = APP[screen_start:screen_end]
    chat_start = APP.index("async function openCrewChat(crew")
    chat_end = APP.index("// ---------- Clubs ----------", chat_start)
    chat = APP[chat_start:chat_end]

    assert "modal.dataset.crewInfoId = String(crew.id);" in screen
    assert "const modalIndex = overlayStack.findIndex((entry) => entry.el === modal);" in chat
    assert "const parent = modalIndex > 0 ? overlayStack[modalIndex - 1]?.el : null;" in chat
    assert "Number(parent?.dataset.crewInfoId) === Number(crew.id)" in chat
    assert "dismissModal(modal);" in chat
    assert "beginButtonAction(infoButton, 'Opening info…')" in chat
    assert "openChildModal(modal, () => openCrewScreen(crew.id))" in chat


def test_postgame_planner_cleanup_only_attaches_to_the_expected_child():
    start = APP.index("async function openPostGamePlanner")
    end = APP.index("function completedCrewConnectionsHtml", start)
    planner = APP[start:end]

    assert "const planner = opened ? currentOverlayEntry()?.el : null;" in planner
    assert "planner && planner !== fromModal" in planner
    assert "planner._cleanupFns?.push" in planner
    assert "if (button?.isConnected) resetAction();" in planner


def test_removed_crew_drafts_are_terminal_and_chat_actions_are_keyboard_accessible():
    assert "if (err.code === 'crew_not_found' && crewId)" in APP
    assert "submitButton.textContent = 'Play group unavailable'" in APP
    assert 'This saved plan was cleared.' in APP
    assert 'type="button" class="chat-message-action"' in APP
    assert 'class="chat-message-row ${mine ? \'is-mine\' : \'is-theirs\'}"' in APP
    assert 'aria-label="Delete your message"' in APP
    assert ".chat-message-action[data-message-action][data-message-id]" in APP
    assert 'role="button" tabindex="0" aria-label="Delete your message"' not in APP
    assert "data-del-msg" not in APP
    assert "data-room-heart" not in APP
    assert "data-heart-msg" not in APP


def test_crew_surfaces_keep_phone_first_layout_language():
    for selector in (
        '.crew-hero', '.crew-primary-actions', '.crew-invite-card',
        '.crew-roster', '.crew-chat-head', '.inbox-room-icon.crew',
    ):
        assert selector in CSS
    assert 'min-height: 46px' in CSS
    assert '#crew-court { cursor: pointer; }' in CSS
    assert 'overflow-wrap: anywhere;' in CSS
    assert '.crew-member .row-title, .crew-member .row-sub { display: block; }' in CSS


def test_crew_detail_consumes_upcoming_games_and_owner_removal_api():
    screen_start = APP.index("async function openCrewScreen")
    screen_end = APP.index("function openRenameCrewSheet", screen_start)
    screen = APP[screen_start:screen_end]
    assert "Array.isArray(crew.upcoming_games)" in screen
    assert 'crewUpcomingGamesHtml(upcomingGames)' in screen
    assert 'data-open-crew-game="${game.id}"' in APP
    assert 'Upcoming play' in screen
    assert 'data-crew-remove-member="${memberId}"' in screen
    assert "api(`/crews/${crew.id}/members/${memberId}`, { method: 'DELETE' })" in screen
    assert 'They will lose access to this private group' in screen


def test_play_group_friend_loaders_keep_retry_and_selection_continuity():
    create_start = APP.index("async function openCreatePlayGroupSheet")
    create_end = APP.index("async function openCrewInviteSheet", create_start)
    create = APP[create_start:create_end]
    invite_start = create_end
    invite_end = APP.index("function crewPlannerOptions", invite_start)
    invite = APP[invite_start:invite_end]

    for source in (create, invite):
        assert "const loadFriends = async () =>" in source
        assert "list.setAttribute('aria-busy', 'true');" in source
        assert "list.innerHTML = skeletonHtml(3);" in source
        assert "renderError(list, error.message ||" in source
        assert "loadFriends);" in source
        assert "let friendsReady = false;" in source
        assert "submit.disabled = true;" in source
        assert "if (!friendsReady)" in source
        assert source.index("addEventListener('submit'") < source.index("loadFriends();")
        assert source.index("event.preventDefault();") < source.index("loadFriends();")
        assert "const liveIds = new Set" in source
        assert "[...selectedIds].forEach" in source

    assert "error.code === 'crew_invitees_changed') await loadFriends();" in create
    assert "await loadFriends();" not in invite
