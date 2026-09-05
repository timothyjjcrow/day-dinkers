"""Focused contracts for the intent-first Play and lightweight onboarding UX."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
INDEX = (ROOT / "public" / "index.html").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(start: str, end: str) -> str:
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_play_launcher_exposes_the_three_plain_play_now_intents_and_planner():
    launcher = section("function rallyLauncherHtml", "async function renderPlay")
    assert launcher.count('data-goto="instant-rally"') == 1
    assert launcher.count('data-goto="on-my-way"') == 1
    assert launcher.count('data-goto="play-pulse"') == 1
    assert launcher.count('data-goto="new-game"') == 1
    assert launcher.count('data-goto="ranked-match"') == 1
    assert 'data-goto="game-flow"' not in launcher
    for label in ("I’m at a court", "I’m on my way", "I’m free this hour", "Create game", "Start a ranked match"):
        assert label in launcher

    flow = section("function openGameFlow", "async function checkInAndStartRally")
    for label in ("Find", "Start now"):
        assert f"> {label}</button>" in flow
    assert 'id="game-flow-tab-schedule"' not in flow
    assert 'id="game-flow-plan-later"' in flow


def test_arriving_choice_uses_live_rallies_and_existing_arrival_backend_flow():
    arriving = section("async function openPlaySoonArrivalChoices", "async function openPlayNowCourtPicker")
    assert "api(`/players/looking?lat=${loc.lat}&lng=${loc.lng}&radius=25`)" in arriving
    assert "normalizeLookingRallies(response)" in arriving
    assert "normalizeLookingPlayersWithoutRally(response)" in arriving
    assert "rally.arrivalAvailable && rally.spotsLeft > 0" in arriving
    assert "openReadyRally(" in arriving
    assert "Players waiting at a court" in arriving
    assert "looking for casual play" in arriving
    assert "data-play-soon-player-court" in arriving
    assert "openCourtDetail(Number(button.dataset.playSoonPlayerCourt))" in arriving
    assert "data-play-soon-coming" in arriving
    assert "api(`/players/${button.dataset.playSoonComing}/coming`" in arriving
    assert "Share that I’m free this hour" in arriving


def test_planner_reveals_where_then_when_then_who_and_never_offers_right_now():
    planner = section("async function openNewGameModal", "async function renderTournaments")
    for step in ("ng-step-where", "ng-step-when", "ng-step-who"):
        assert f'id="{step}"' in planner
    assert 'id="ng-next-when"' in planner
    assert 'id="ng-next-who"' in planner
    assert "syncPlannerStep" in planner
    assert "plannerStep = 'when'" in planner
    assert "plannerStep = 'who'" in planner
    assert 'data-mode="now"' not in planner
    assert "Start game now" not in planner


def test_when_starts_with_exactly_three_smart_choices_and_discloses_any_other_time():
    planner = section("async function openNewGameModal", "async function renderTournaments")
    assert "const smartTimeSuggestions = []" in planner
    assert "smartTimeSuggestions.length >= 3" in planner
    assert planner.count("smartTimeSuggestions.length < 3") == 2
    assert 'id="ng-smart-times" role="group" aria-label="Suggested ${defaultType === \'ranked\' ? \'match\' : \'play session\'} times"' in planner
    assert "syncPlannerNounLabels" in planner
    assert 'data-smart-time="${slot.date.toISOString()}"' in planner
    assert 'id="ng-day-strip" role="radiogroup" aria-label="Play date"' in planner
    assert 'id="ng-time-grid" role="radiogroup" aria-label="Play time in 30-minute steps"' in planner
    assert 'Array.from({ length: 31 }, (_, index) => 6 + index / 2)' in planner
    assert '<label for="ng-when">Date and time</label>' in planner
    assert 'aria-label="${defaultType === \'ranked\' ? \'Match\' : \'Play session\'} date and time"' in planner
    assert 'id="ng-days"' not in planner
    assert 'id="ng-hours"' not in planner
    assert "dayChips" not in planner
    assert "timeChips" not in planner


def test_answered_planner_questions_become_accessible_summaries_with_change_actions():
    planner = section("async function openNewGameModal", "async function renderTournaments")
    assert 'id="ng-answer-where" role="group" aria-label="Where"' in planner
    assert 'id="ng-back-where" aria-label="Change where">Change</button>' in planner
    assert 'id="ng-answer-when" role="group" aria-label="When"' in planner
    assert 'id="ng-back-when" aria-label="Change when">Change</button>' in planner
    assert "whereAnswer.classList.toggle('hidden', plannerStep === 'where')" in planner
    assert "whenAnswer.classList.toggle('hidden', plannerStep !== 'who')" in planner
    assert "answer.setAttribute('aria-hidden', String(answer.classList.contains('hidden')))" in planner


def test_planner_explains_empty_friends_visibility_and_calls_advanced_settings_more_options():
    planner = section("async function openNewGameModal", "async function renderTournaments")
    assert 'id="ng-friends-empty" role="status"' in planner
    assert "initialVisibility === 'friends' && friends.length === 0" in planner
    assert "button.dataset.vis === 'friends' && friends.length === 0" in planner
    assert "'Add friends first'" in planner
    assert '<summary><span>More options</span>' in planner
    assert '<summary><span>Game options</span>' not in planner


def test_crew_plans_choose_group_friends_or_nearby_before_scheduling():
    planner = section("async function openNewGameModal", "async function renderTournaments")
    assert 'id="ng-crew-private" role="status"' in planner
    assert "Starts with ${esc(crewName || 'your play group')}" in planner
    assert 'id="ng-step-who" aria-labelledby="planner-who-title"' in planner
    for label in ("Group only", "Friends", "Nearby players"):
        assert f'<b>{label}</b>' in planner
    assert "const finalStep = plannerStep === 'who'" in planner
    assert "if (crewId && btn.dataset.vis !== 'private') return;" not in planner


def test_pickup_game_language_is_here_on_the_way_and_open():
    counts = section("function rallyCountsText", "function arrivalEtaLabel")
    assert "here" in counts
    assert "on the way" in counts
    assert "open" in counts
    for old in ("physically ready", "at the court", "arriving", "spots left"):
        assert old not in counts

    arrival = section("function openRallyArrivalSheet", "async function cancelRallyArrival")
    assert '<p class="arrival-summary" role="status">${esc(rallyCountsText(rally))}</p>' in arrival
    assert '<div class="arrival-summary"' not in arrival
    assert "spots left" not in arrival  # singular/plural is rendered from the count
    assert "Retry safely" not in arrival
    assert "The server" not in arrival


def test_fill_game_uses_one_inline_invite_row_and_a_focused_channel_panel():
    launcher = section("function rosterBoostLauncherHtml", "function openRosterBoostSheet")
    fill = section("function openRosterBoostSheet", "function crewSummaryFrom")
    assert '<b id="gs-invite-title">Invite</b>' in launcher
    for channel, label in (
        ("friends", "Friends"), ("court", "Court chat"), ("share", "Share link"),
    ):
        assert f'data-roster-boost-channel="{channel}"' in launcher
        assert f'<b>{label}</b>' in launcher
        assert f'data-rb-channel="{channel}"' in fill
    assert 'role="tablist" aria-label="Invite options"' in fill
    assert 'id="rb-friends-channel"' in fill
    assert 'id="rb-court-channel"' in fill
    assert 'id="rb-share-channel"' in fill
    assert 'role="tabpanel"' in fill
    assert "panel.classList.toggle('hidden', name !== channel)" in fill
    assert "button.setAttribute('aria-selected', String(selected))" in fill
    assert "['ArrowLeft', 'ArrowRight']" in fill
    assert 'id="rb-receipts"' not in fill
    assert 'Loading your play group' not in fill


def test_join_becomes_a_stable_open_action_and_waitlist_remains_manageable():
    cards = section("function gameCardHtml", "// Share text")
    assert "`${uiIcon('check')} Joined · Open ${esc(playNoun)}`" in cards
    assert "showJoinedToast(Number(b.dataset.gameJoin)" in cards
    assert "label: 'Undo'" in cards
    assert "Waitlisted · Leave" in cards
    assert 'data-game-waitlist-manage="${game.id}"' in cards
    assert "setTimeout(refresh, 4000)" in cards
    assert "openGameScreen(Number(b.dataset.gameWaitlistManage))" in cards

    detail = section("async function openGameScreen", "function safeNotificationOverlayRoute")
    assert "const rememberFresh = (fresh) =>" in detail
    assert "showJoinedToast(gameId" in detail
    assert "render(fresh);" in detail
    assert "button.dataset.undoJoin" not in detail
    assert "const fresh = await api(`/games/${gameId}/waitlist`" in detail
    assert "render(fresh);" in detail
    assert "maybeOfferPhoneNotifications('Get a ping if a spot opens?')" in detail


def test_home_area_onboarding_is_optional_account_scoped_and_has_one_settings_destination():
    onboarding = section("function homeAreaOnboardingKey", "// One-time 3-step welcome tour")
    assert "`pp_onboarded_home:${id}`" in onboarding
    assert "Optional: choose a home area" in onboarding
    assert "Maybe later" in onboarding
    assert "openHomeAreaOnboarding()" in onboarding
    assert "maybeSuggestStarterCourts" not in onboarding
    assert "maybeShowTour" not in onboarding

    privacy = section("function openPrivacySafetySettings", "function openAppearanceSettings")
    assert 'id="privacy-home-area"' in privacy
    assert 'id="privacy-replay-setup"' not in privacy
    assert "Quick setup" not in privacy
    assert "openChildModal(modal, () => openHomeAreaSheet" in privacy
    assert "syncPrivacyControls();" in privacy
    assert "renderProfile();" in privacy


def test_home_area_city_search_is_labeled_race_safe_and_explains_empty_or_failed_results():
    search = section("function bindCitySearch", "// Home-area picker")
    assert "let searchSeq = 0;" in search
    assert "const seq = ++searchSeq;" in search
    assert "seq !== searchSeq || input.value.trim() !== q" in search
    assert "renderFeedback('Searching cities…')" in search
    assert "No cities found for “${q}”." in search
    assert "Couldn’t search cities. Check your connection and try again." in search
    assert "resultsEl.setAttribute('aria-busy', 'true')" in search
    assert "resultsEl.removeAttribute('aria-busy')" in search

    sheet = section("function openHomeAreaSheet", "async function maybeSuggestStarterCourts")
    assert '<label for="ha-city">Search by city</label>' in sheet
    assert '<div id="ha-results" aria-live="polite"></div>' in sheet


def test_community_group_search_has_an_accessible_name():
    sheet = section("async function openFindClubsSheet", "async function openCourtGallery")
    assert '<label class="sr-only" for="fc-search">Search community groups</label>' in sheet


def test_play_shell_has_primary_segments_and_one_contextual_create_action():
    setup = section("function setupPlay", "function openCompetitionCreateSheet")
    assert "$('#play-segments')?.addEventListener" in setup
    assert "$('#new-game-fab')?.addEventListener" in setup
    assert "function setPlaySegment" in setup
    assert "['games', 'scores', 'brackets'].includes(segment)" in setup
    assert "fab.classList.toggle('hidden', state.playSeg === 'games')" in setup
    for segment in ('games', 'scores', 'brackets'):
        assert f'data-seg="{segment}"' in INDEX
    assert 'id="new-game-fab" class="fab"' in INDEX
    assert "function playMoreRoutesHtml" not in APP
    assert 'data-play-route=' not in APP


def test_play_launcher_survives_independent_feed_failures_with_honest_retry_states():
    play = section("async function renderPlay", "function updatePlayHeader")
    assert "api(homeUrl)" in play
    assert "const settled = await Promise.allSettled([" in play
    assert "api('/games?mine=1')" in play
    assert "api(`/games?friends=1${areaQuery}${levelQuery}`)" in play
    assert "const mineFeed = feedResult(settled[0]" in play
    assert "const friendsFeed = feedResult(settled[1]" in play
    assert "const nearbyFeed = feedResult(settled[2]" in play
    assert "let html = rallyLauncherHtml();" in play
    assert "if (feedErrors.mine)" in play
    assert "Finding, starting, and scheduling play still work." in play
    # Friend sessions have their own rail and error state; only the nearby
    # endpoint can make the nearby-discovery rail partial.
    assert "const discoveryFeedFailed = !!feedErrors.nearby;" in play
    assert "Friends’ sessions did not load" in play
    assert "Nearby play did not load" in play
    assert 'data-play-feed-retry' in play
    assert "state.playGamesCache = null;" in play
    assert ".play-feed-state" in STYLES
    assert ".play-feed-state .btn" in STYLES
