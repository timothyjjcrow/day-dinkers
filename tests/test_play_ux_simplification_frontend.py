"""Focused contracts for the intent-first Play and lightweight onboarding UX."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()


def section(start: str, end: str) -> str:
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_play_launcher_has_one_shared_immediate_entry_and_one_schedule_entry():
    launcher = section("function rallyLauncherHtml", "function playMoreRoutesHtml")
    assert launcher.count('data-goto="game-flow"') == 1
    assert launcher.count('data-goto="new-game"') == 1
    assert 'data-goto="play-soon"' not in launcher
    assert 'data-goto="play-now"' not in launcher
    assert 'data-goto="play-pulse"' not in launcher
    assert "const immediateAction = `<button" in launcher
    assert 'data-goto="instant-rally"' not in launcher
    assert "Find or start a game" in launcher
    assert "Casual or Ranked · Singles or Doubles" in launcher
    assert "Plan a game" in launcher

    flow = section("function openGameFlow", "async function checkInAndStartRally")
    for label in ("Find", "Start now", "Schedule"):
        assert f"> {label}</button>" in flow


def test_arriving_choice_uses_live_rallies_and_existing_arrival_backend_flow():
    arriving = section("async function openPlaySoonArrivalChoices", "async function openPlayNowCourtPicker")
    assert "api(`/players/looking?lat=${loc.lat}&lng=${loc.lng}&radius=25`)" in arriving
    assert "normalizeLookingRallies(response)" in arriving
    assert "rally.arrivalAvailable && rally.spotsLeft > 0" in arriving
    assert "openReadyRally(" in arriving
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
    assert 'id="ng-smart-times" role="group" aria-label="Suggested game times"' in planner
    assert 'data-smart-time="${slot.date.toISOString()}"' in planner
    assert '<summary>Choose another time</summary>' in planner
    assert 'id="ng-when" aria-label="Game date and time"' in planner
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
    assert "visibility !== 'friends' || friends.length > 0" in planner
    assert "Add friends from Community" in planner
    assert '<summary><span>More options</span>' in planner
    assert '<summary><span>Game options</span>' not in planner


def test_crew_plans_skip_who_and_end_with_a_private_crew_summary():
    planner = section("async function openNewGameModal", "async function renderTournaments")
    assert 'id="ng-crew-private" role="status"' in planner
    assert "🔒 Private to ${esc(crewName || 'your crew')}" in planner
    assert 'id="ng-step-who" aria-hidden="true"' in planner
    assert "const finalStep = crewId ? plannerStep === 'when'" in planner
    assert "visibility = crewId ? 'private'" in planner


def test_live_rally_language_is_at_court_arriving_and_spots_left():
    counts = section("function rallyCountsText", "function arrivalEtaLabel")
    assert "at the court" in counts
    assert "arriving" in counts
    assert "left" in counts
    for old in ("physically ready", "on the way", "spots open"):
        assert old not in counts

    arrival = section("function openRallyArrivalSheet", "async function cancelRallyArrival")
    assert "at the court" in arrival
    assert "arriving" in arrival
    assert "spots left" not in arrival  # singular/plural is rendered from the count
    assert "Retry safely" not in arrival
    assert "The server" not in arrival


def test_fill_game_hides_empty_channels_and_promotes_the_best_available_action():
    fill = section("function openRosterBoostSheet", "function crewSummaryFrom")
    assert 'id="rb-friends-channel"' in fill
    assert 'id="rb-court-channel"' in fill
    assert 'id="rb-share-channel"' in fill
    assert "friendsSection.classList.toggle('hidden', candidates.length === 0)" in fill
    assert "const hasCourtAction = hasCourtPost && (canManageCourtPost || canCreateCourtPost);" in fill
    assert "postButton.classList.toggle('btn-primary', !full && !hasFriends && hasCourtAction)" in fill
    assert "shareButton.classList.toggle('btn-primary', !full && !hasFriends && !hasCourtAction)" in fill
    assert "const usableChannels = [" in fill
    assert "...availableChannels.filter((section) => !usableChannels.includes(section))" in fill
    assert "No friends here yet" not in fill


def test_join_and_waitlist_confirm_in_place_before_refreshing():
    cards = section("function gameCardHtml", "// Share text")
    assert "Joined ✓ · Undo" in cards
    assert "Waitlisted · Leave" in cards
    assert 'data-game-waitlist-manage="${game.id}"' in cards
    assert "setTimeout(refresh, 4000)" in cards
    assert "openGameScreen(Number(b.dataset.gameWaitlistManage))" in cards

    detail = section("async function openGameScreen", "function safeNotificationOverlayRoute")
    assert "const rememberFresh = (fresh) =>" in detail
    assert "button.dataset.undoJoin = 'true'" in detail
    assert "button.textContent = 'Joined ✓ · Undo'" in detail
    assert "button.dataset.undoWaitlist = 'true'" in detail
    assert "button.dataset.confirmationLabel = position ? `Waitlisted #${position} · Leave`" in detail
    assert "setTimeout(() => render(game), 4000)" in detail


def test_home_area_onboarding_is_optional_account_scoped_and_replayable():
    onboarding = section("function homeAreaOnboardingKey", "// One-time 3-step welcome tour")
    assert "`pp_onboarded_home:${id}`" in onboarding
    assert "Optional: choose a home area" in onboarding
    assert "Maybe later" in onboarding
    assert "openHomeAreaOnboarding()" in onboarding
    assert "maybeSuggestStarterCourts" not in onboarding
    assert "maybeShowTour" not in onboarding

    privacy = section("function openPrivacySafetySettings", "function openAppearanceCalendarSettings")
    assert 'id="privacy-replay-setup"' in privacy
    assert "openHomeAreaOnboarding({ replay: true, onComplete: renderProfile })" in privacy


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

    sheet = section("function openHomeAreaSheet", "// Onboarding step 2")
    assert '<label class="sr-only" for="ha-city">Search for your home city</label>' in sheet
    assert '<div id="ha-results" aria-live="polite"></div>' in sheet


def test_play_setup_tolerates_removed_shell_controls_and_keeps_content_routes():
    setup = section("function setupPlay", "function openCompetitionCreateSheet")
    assert "$('#play-segments')?.addEventListener" in setup
    assert "$('#new-game-fab')?.addEventListener" in setup
    routes = section("function playMoreRoutesHtml", "async function renderPlay")
    assert 'data-play-route="scores"' in routes
    assert 'data-play-route="brackets"' in routes
    assert "state.playSeg = button.dataset.playRoute" in routes
