"""Contracts for result deadlines, nudges, league urgency, and coordination."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()
STYLES = (ROOT / 'public' / 'styles-v15.css').read_text()


def section(start, end):
    return APP[APP.index(start):APP.index(end, APP.index(start))]


def test_me_state_owns_and_clears_the_active_league_banner():
    state = section('const state = {', 'const pageNotifications')
    apply_me = section('function applyMe', 'function dismissedInvites')
    logout = section('function resetPrivateUiForLogout', 'function logout')
    banner = section('function renderActiveGameBanner', 'function syncCommunityUnreadLanes')

    assert 'activeTournament: null' in state
    assert 'activeLeague: null' in state
    assert 'state.activeLeague = data.active_league || null;' in apply_me
    assert 'state.activeTournament = null;' in logout
    assert 'state.activeLeague = null;' in logout
    assert "['confirm', 'resolve', 'play'].includes(league.banner_state)" in banner
    assert "t?.banner_state === 'live' && renderTournamentBanner(el)" in banner
    assert 'leagueWins && renderActiveLeagueBanner(el)' in banner
    assert 'function renderActiveLeagueBanner(el)' in banner
    assert "openLeagueScreen(league.id, league.action_match_id || null)" in banner
    for banner_state in ('confirm', 'resolve', 'play', 'active'):
        assert f'{banner_state}:' in banner
    assert 'league.round_deadline_at' in banner


def test_deadlines_are_clamped_render_exact_time_and_use_cleanup_safe_ticks():
    helpers = section('function competitionDeadlineState', 'function tournamentCheckinState')
    sheet = section('function openCompetitionResultSheet', '// ---------- Box leagues')

    assert 'Math.max(0, Math.ceil(remainingMs / 60000))' in helpers
    assert "remainingMs <= 0" in helpers
    assert 'nearDue: remainingMs > 0 && remainingMs <= 48 * 60 * 60 * 1000' in helpers
    assert "deadline.nearDue ? ' is-near'" in helpers
    assert "expiredLabel" in helpers
    assert '<time datetime=' in helpers
    assert 'match.review_deadline_at' in sheet
    assert "Confirmation window ended" in sheet
    assert 'setTimeout(runTemporalTick, 30000)' in sheet
    assert 'modal._cleanupFns?.push(() => clearTimeout(temporalTimer));' in sheet
    assert 'setInterval(' not in sheet
    # The minute tick updates only deadline/cooldown UI, never score inputs.
    temporal = sheet[sheet.index('const syncTemporalResult'):sheet.index('const syncVisibleResult')]
    assert 'score1.value' not in temporal
    assert 'score2.value' not in temporal


def test_automatic_confirmation_has_friendly_status_and_provenance():
    status = section('function competitionResultStatusHtml', 'function competitionResultHistoryHtml')

    assert 'match.confirmed_automatically' in status
    assert "'Confirmed automatically'" in status
    assert "match.resolution_kind !== 'automatic_timeout'" in status
    assert 'Resolution: automatic timeout' not in status


def test_nudge_uses_server_capability_version_cooldown_and_both_response_shapes():
    sheet = section('function openCompetitionResultSheet', '// ---------- Box leagues')
    action = section('function competitionActionNeeded', 'function competitionActionNeededHtml')

    assert 'competitionNudgeState(match)' in sheet
    assert 'data-result-nudge' in sheet
    assert "`/${plural}/${liveParent.id}/matches/${match.id}/nudge`" in sheet
    assert 'result_version: Number(match.result_version || 0)' in sheet
    assert 'Array.isArray(response?.matches)' in sheet
    assert 'match = { ...match, ...(response || {}) };' in sheet
    assert "response?.already_sent" in sheet
    assert "refreshStaleResult('nudge', err.data)" in sheet
    assert "closeModal(modal)" not in sheet[
        sheet.index("modal.querySelector('[data-result-nudge]')"):
        sheet.index("modal.querySelectorAll('[data-result-action]')")
    ]
    assert "modal.querySelector('[data-result-action]:not([disabled])')?.click();" in sheet
    assert "result.state === 'awaiting_confirmation' && match.can_nudge_result" in action
    assert 'Nudge the confirmer or finalize the result.' in action


def test_league_deadlines_show_on_cards_matches_details_and_blocker_copy():
    league_card = section('function leagueCardHtml', 'function competitionDetailTabsHtml')
    match_card = section('function leagueMatchCardHtml', 'async function openLeagueScreen')
    league_screen = section('async function openLeagueScreen', 'async function openLeagueChat')

    assert 'lg.round_deadline_at' in league_card
    assert 'match.review_deadline_at' in match_card
    assert "result.state === 'unreported' && parent?.round_deadline_at" in match_card
    assert "absolutePrefix: 'Play by'" in match_card
    assert 'lg.round_deadline_at' in league_screen
    assert 'competition-round-deadline' in league_screen
    assert 'it will count as not played when the round closes' in APP
    assert 'must be confirmed, decided, or marked not played before the round can close' in league_screen
    assert 'Moves up when the round closes' in league_screen
    assert 'Moves down when the round closes' in league_screen


def test_opponent_actions_use_real_users_and_preserve_a_proposed_time_draft():
    opponents = section('function competitionOpponents', 'function competitionResultStatusHtml')
    sheet = section('function openCompetitionResultSheet', '// ---------- Box leagues')
    thread = section('async function openThread', 'function courtOpenCallFingerprint')

    assert 'Array.isArray(entry?.players) ? entry.players : []' in opponents
    assert "Number(player?.id) === viewer" in opponents
    assert 'if (viewerSide < 0) return [];' in opponents
    assert "data-opponent-profile" in sheet
    assert "data-opponent-message" in sheet
    assert "data-opponent-propose" in sheet
    assert "liveParent.status === 'active' && result.state === 'unreported'" in sheet
    assert 'openUserProfile(Number(button.dataset.opponentProfile))' in sheet
    assert 'openThread(Number(button.dataset.opponentMessage))' in sheet
    assert '{ draft }' in sheet

    assert "async function openThread(userId, { draft = '' } = {})" in thread
    assert 'openThread(userId, { draft })' in thread
    continuity = thread.index('bindChatContinuity(modal, msgsEl, input')
    seeding = thread.index('if (draft && !input.value)')
    assert continuity < seeding
    assert "String(draft).slice(0, limit)" in thread
    assert "input.dispatchEvent(new Event('input', { bubbles: true }))" in thread


def test_competition_coordination_controls_are_mobile_safe():
    for selector in (
        '.competition-deadline', '.competition-opponents',
        '.competition-opponent-row', '.competition-opponent-actions',
        '.competition-nudge',
    ):
        assert selector in STYLES
    assert '.competition-opponent-actions .btn, .competition-nudge { min-height: 44px; }' in STYLES
    assert '.competition-deadline.is-near { color: var(--amber-800); }' in STYLES
    assert '.competition-deadline.is-expired { color: var(--red-700); }' in STYLES


def test_hub_cards_are_truthful_when_full_or_waiting_and_show_attention():
    cards = section('function tournamentStatusChip', 'function competitionDetailTabsHtml')

    assert "competitionStatusTag('Registration full'" in cards
    assert "competitionStatusTag('Waiting for organizer to start'" in cards
    assert "competitionStatusTag(`Signups full · ${lg.member_count} players`" in cards
    assert 'function competitionPendingActionHtml' in cards
    assert "action${count === 1 ? '' : 's'} waiting for you" in cards
    assert cards.count('competitionPendingActionHtml(') >= 3


def test_match_cards_have_direct_opponent_actions_without_hijacking_drill_in():
    helpers = section('function competitionOpponents', 'function competitionResultStatusHtml')
    league_card = section('function leagueMatchCardHtml', 'async function openLeagueScreen')
    tournament_cards = section('function bracketHtml', 'function tournamentPartnerPickerHtml')

    for token in (
        'data-card-opponent-profile', 'data-card-opponent-message',
        'data-card-opponent-propose', 'event.stopPropagation()',
        "openThread(Number(target.dataset.cardOpponentMessage))",
    ):
        assert token in helpers
    assert "competitionCardOpponentActionsHtml('league'" in league_card
    assert "competitionCardOpponentActionsHtml('tournament'" in tournament_cards


def test_round_picker_my_matches_and_tbd_safety_are_real_controls():
    controls = section('function competitionRoundControlsHtml', 'function leagueMatchCardHtml')
    league = section('async function openLeagueScreen', 'function openEditLeagueSheet')
    tournament_cards = section('function bracketHtml', 'function tournamentPartnerPickerHtml')
    tournament = section('async function openTournamentScreen', 'function openEditTournamentSheet')

    assert 'My matches' in controls
    assert 'selectedLeagueRound' in league
    assert 'lg-round-filter' in league
    assert 'lg-mine-filter' in league
    assert 'match_history' in league
    assert "hasBothSides ? `data-tmatch=" in tournament_cards
    assert 'aria-label="Matchup not set yet"' in tournament_cards
    assert 'selectedTournamentRound' in tournament
    assert 'td-round-filter' in tournament
    assert 'td-mine-filter' in tournament


def test_entries_show_both_players_and_each_profile_is_tappable():
    tournament = section('async function openTournamentScreen', 'function openEditTournamentSheet')

    assert 'competition-entry-avatars' in tournament
    assert 'competition-entry-avatar' in tournament
    assert 'competition-entry-person-name' in tournament
    assert 'data-view-user="${Number(player.id)}"' in tournament
    assert 'bindUserButtons(box);' in tournament
    assert 'avatarHtml(en.players[0]' not in tournament


def test_competition_sharing_settings_chat_and_polling_are_complete():
    league = section('async function openLeagueScreen', 'function openEditLeagueSheet')
    league_edit = section('function openEditLeagueSheet', 'async function openLeagueChat')
    tournament = section('async function openTournamentScreen', 'function openEditTournamentSheet')

    assert 'id="lg-share"' in league
    assert 'id="lg-ics"' in league
    assert 'Add season dates' in league
    assert '`RRULE:FREQ=DAILY;INTERVAL=${roundDays}`' in APP
    assert 'id="lg-edit"' in league
    assert "openChildModal(box, () => openLeagueChat(lg))" in league
    assert "leagueNav.push(['lg-chat'" not in league
    assert "!['registration', 'active'].includes(lg.status)" in league
    for field in (
        'le-name', 'le-court-search', 'le-court-id', 'le-when',
        'le-box', 'le-max', 'le-round-days',
    ):
        assert f'id="{field}"' in league_edit
    assert "clubCourtPicker(modal, 'le')" in league_edit
    assert 'court_id: courtId' in league_edit
    assert "method: 'PATCH'" in league_edit

    assert 'id="td-share"' in tournament
    assert 'id="td-ics"' in tournament
    assert "shareCompetition('tournament', t)" in tournament
    assert "openChildModal(box, () => openTournamentChat(t))" in tournament
    assert "tournamentNav.push(['td-chat'" not in tournament
    assert "!['registration', 'active'].includes(t.status)" in tournament


def test_result_sheet_uses_player_language_and_supports_forfeits():
    sheet = section('function openCompetitionResultSheet', '// ---------- Box leagues')

    for copy in (
        'Report score', 'Looks right — confirm', 'That score is not right',
        'Set final score', 'Mark as not played', 'No-show or forfeit',
        'data-result-action="forfeit-1"', 'data-result-action="forfeit-2"',
    ):
        assert copy in sheet
    for stale_copy in (
        'Submit score for confirmation', 'Resolve & finalize',
        'Void this result', 'sat-out',
    ):
        assert stale_copy not in sheet


def test_new_competition_surfaces_remain_phone_safe():
    for selector in (
        '.competition-card-attention', '.competition-card-opponents',
        '.competition-match-filters', '.competition-entry-avatars',
        '.competition-share-actions', '.competition-standings-legend',
        '.competition-forfeit-controls',
    ):
        assert selector in STYLES
    assert '.competition-match-filters, .competition-share-actions { grid-template-columns: 1fr; }' in STYLES


def test_tournament_setup_and_zero_entry_editor_cover_format_division_and_capacity():
    create = section('async function openCreateTournamentSheet', 'function tournamentTitlesHtml')
    edit = section('function openEditTournamentSheet', 'async function openTournamentChat')

    for field in ('tc-division', 'tc-game-format', 'tc-court-count', 'tc-match-minutes'):
        assert f'id="{field}"' in create
    for payload_field in (
        'game_format:', 'division_name:', 'division_min_rating:',
        'division_max_rating:', 'court_count:', 'match_minutes:',
    ):
        assert payload_field in create
    assert 'A rated division is enforced when players sign up.' in create
    assert 'id="tc-schedule-estimate"' in create
    assert 'const syncTournamentEstimate = () =>' in create
    assert 'matches across ${rounds} rounds' in create
    assert "const canEditStructure = inRegistration && Number(t.entry_count || 0) === 0;" in edit
    for field in (
        'te-court-search', 'te-format', 'te-event', 'te-division',
        'te-game-format', 'te-courts', 'te-match-minutes', 'te-ranked',
    ):
        assert f'id="{field}"' in edit
    assert "if (canEditStructure) clubCourtPicker(modal, 'te');" in edit
    assert 'entries_lock_tournament_format' in APP


def test_tournament_result_sheet_collects_per_game_scores_and_rejects_extra_games():
    sheet = section('function openCompetitionResultSheet', '// ---------- Box leagues')

    assert "const usesTournamentGameLedger = kind === 'tournament';" in sheet
    assert "liveParent.game_format === 'best_of_3_11' ? 3 : 1" in sheet
    assert 'data-competition-game-row' in sheet
    assert 'data-competition-game-score="1"' in sheet
    assert 'return { games };' in sheet
    assert 'const validFinish = (high === target && margin >= 2) || (high > target && margin === 2);' in sheet
    assert 'Remove games entered after the match was already decided.' in sheet


def test_tournament_match_cards_show_and_edit_time_and_court_without_opening_result():
    helpers = section('// ---------- Tournaments ----------', 'function competitionStatusTag')
    tournament = section('async function openTournamentScreen', 'function openEditTournamentSheet')

    assert 'function tournamentMatchScheduleHtml' in helpers
    assert 'function tournamentGameScoresText' in helpers
    assert 'function tournamentScheduleActionHtml' in helpers
    assert 'function openTournamentMatchScheduleSheet' in APP
    assert "api(`/tournaments/${tournament.id}/matches/${match.id}/schedule`" in APP
    assert 'data-edit-tournament-schedule' in tournament
    schedule_binding = tournament[tournament.index("content.querySelectorAll('[data-edit-tournament-schedule]'"):]
    assert schedule_binding.index('event.stopPropagation();') < schedule_binding.index('openTournamentMatchScheduleSheet(')


def test_tournament_arrival_and_league_completion_copy_are_operational_and_honest():
    tournament = section('async function openTournamentScreen', 'function openEditTournamentSheet')
    league = section('async function openLeagueScreen', 'function openEditLeagueSheet')

    assert 'data-tournament-arrival-countdown' in tournament
    assert 'Arrival status opens in' in tournament
    assert 'planned start time reached' in tournament
    assert 'const hereCount = (t.entries || []).filter((entry) => entry.checked_in).length;' in tournament
    assert "if (!['registration', 'active'].includes(t.status)) { clearInterval(poll); return; }" in tournament
    assert '<details class="competition-organizer-tools">' in league
    assert 'Finish season' in league
    assert 'not played' in league
    assert 'would move up' in league
    assert 'would move down' in league
    assert 'Leave this league?' in league
    assert 'Leave league' in league
    assert 'data-remove-league-member' in league
    assert "method: 'DELETE'" in league

    assert '.competition-arrival-summary' in STYLES
    assert '.competition-match-schedule-action { min-height: var(--tap-min);' in STYLES
    assert '.competition-organizer-tools > summary {' in STYLES
    assert 'min-height: var(--tap-min);' in STYLES


def test_competition_chat_headers_keep_the_competition_name_and_room_type():
    league_chat = section('async function openLeagueChat', 'async function openCreateLeagueSheet')
    tournament_chat = section('async function openTournamentChat', '// ---------- Chat & Friends ----------')

    assert '<div class="row-title">${esc(lg.name)}</div>' in league_chat
    assert 'League chat — only players in this league can read it' in league_chat
    assert '<div class="row-title">${esc(data.tournament.name)}</div>' in tournament_chat
    assert 'Tournament chat — ${Number(t.entry_count || 0)}' in tournament_chat
