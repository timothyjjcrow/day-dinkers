"""Focused browser contracts for the private Crew loop."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()
CSS = (ROOT / 'public' / 'styles-v15.css').read_text()


def test_completed_game_creates_crew_before_reusing_source_roster_planner():
    assert "api(`/games/${game.id}/crew`, { method: 'POST' })" in APP
    assert "crewRequest || api(`/games/${game.id}/crew`)" in APP
    assert "'👥 Create crew &amp; plan next game'" in APP
    assert 'Crew created${invitedCount' in APP
    # New Crew invitees are pending, so the first plan safely reuses the
    # completed-game snapshots without falsely attaching an owner-only roster.
    assert 'completedCrewPlannerOptions(game, crew, { ...savedCrew, attachCrew: false })' in APP
    assert 'id="gs-open-crew"' in APP
    assert 'id="cel-open-crew"' in APP
    # A previously-created Crew must load its authoritative accepted roster;
    # network/detail failure cannot silently become an editable normal invite.
    assert 'Once a Crew already exists, its accepted member list is the privacy' in APP
    assert 'options = crewPlannerOptions({ ...detail, ...crewSummaryFrom(detail) })' in APP
    assert 'source-game invitees remain a safe fallback' not in APP


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


def test_attached_crew_planner_locks_the_server_owned_roster_and_recurrence():
    assert 'id="ng-crew-private"' in APP
    assert '🔒 Private to ${esc(crewName || \'your crew\')}' in APP
    assert 'id="ng-step-who" aria-hidden="true"' in APP
    assert 'if (!btn || btn.disabled || crewId) return;' in APP
    assert 'plannedPlayerCount = crewId ? invitePeople.length + 1' in APP
    assert "recurrence: crewId ? 'none'" in APP
    assert "const recurringAllowed = !crewId && !isRanked" in APP


def test_stale_crew_version_refreshes_full_member_detail_before_resubmit():
    assert "if (err.code === 'crew_changed' && crewId)" in APP
    assert 'const detail = await api(`/crews/${crewId}`)' in APP
    assert 'const schedulable = applyFreshCrewRoster(detail)' in APP
    assert 'detail.members' in APP
    assert 'crewVersion = summary.roster_version' in APP
    assert "submitButton.textContent = 'Refreshing Crew roster…'" in APP


def test_community_has_crews_pending_invites_and_response_actions():
    assert "api('/crews/mine').catch(() => ({ items: [], invitations: [] }))" in APP
    assert "kind: 'crew', id: crew.id" in APP
    assert '>Crew invitations<' in APP
    assert 'data-crew-response="accept"' in APP
    assert 'data-crew-response="decline"' in APP
    assert "api(`/crews/${crewId}/respond`" in APP
    assert 'body: JSON.stringify({ accept })' in APP
    assert "else if (kind === 'crew') await openCrewChatById(id);" in APP
    assert '>Decline</button>' in APP


def test_pending_invite_routes_fall_back_to_the_consent_card():
    assert "const mine = await api('/crews/mine')" in APP
    assert 'Number(crewSummaryFrom(invitation)?.id) === Number(crewId)' in APP
    assert 'showCommunityInbox();' in APP
    assert "toast('Crew invitation ready — choose Join crew or Decline')" in APP


def test_crew_home_management_chat_outbox_and_hash_route_are_wired():
    assert "api(`/crews/${crewId}`)" in APP
    assert '📅 Plan a game' in APP
    assert "api(`/crews/${crew.id}`, { method: 'PATCH'" in APP
    assert "api(`/crews/${crew.id}/leave`, { method: 'POST' })" in APP
    assert "api(`/crews/${crew.id}`, { method: 'DELETE' })" in APP
    assert "api(`/crews/${crew.id}/chat`)" in APP
    assert 'bindChatContinuity(modal, msgsEl, input, `crew:${crew.id}`)' in APP
    assert "crew: 'crews'" in APP
    assert "'court', 'game', 'tournament', 'club', 'crew', 'league'" in APP
    assert "else if (route.kind === 'crew') openCrewScreen(route.id);" in APP
    assert "prepareRoute('crew', id); openCrewScreen(id);" in APP
    assert "await purgeChatOutboxChannel(state.me?.id, `crew:${crew.id}`);" in APP
    assert "item.channelKey.startsWith('crew:')" in APP


def test_removed_crew_drafts_are_terminal_and_chat_actions_are_keyboard_accessible():
    assert "if (err.code === 'crew_not_found' && crewId)" in APP
    assert "submitButton.textContent = 'Crew unavailable'" in APP
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
    assert 'button.crew-member .row-title, button.crew-member .row-sub { display: block; }' in CSS
