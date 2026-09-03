"""Focused contracts for the auth, onboarding, and launch audit slice."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
INDEX = (ROOT / "public" / "index.html").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()
AUTH_BACKEND = (ROOT / "backend" / "routes" / "auth.py").read_text()


def section(start: str, end: str) -> str:
    offset = APP.index(start)
    return APP[offset:APP.index(end, offset)]


def test_auth_failures_offer_accessible_recovery_paths_without_losing_input():
    assert 'id="auth-error-copy"' in INDEX
    assert 'id="auth-error-action" class="auth-error-action hidden"' in INDEX
    auth = section("function setupAuth()", "function purgeAccountChatDrafts")
    assert "errorAction.dataset.authDestination = action.destination;" in auth
    assert "{ label: 'Log in instead', destination: 'login' }" in auth
    assert "{ label: 'Create an account instead', destination: 'register' }" in auth
    assert "moveToAuthDestination(errorAction.dataset.authDestination)" in auth
    assert "openForgotPassword();" in auth
    assert "min-height: var(--tap-min)" in STYLES.split(".auth-error-action {", 1)[1].split("}", 1)[0]


def test_password_and_submit_controls_are_explicit_and_browser_bubbles_are_avoided():
    assert '<form id="auth-form" novalidate' in INDEX
    assert 'id="auth-password-toggle" aria-label="Show password" aria-pressed="false"' in INDEX
    assert "a longer passphrase is safer" in INDEX
    auth = section("function setupAuth()", "function purgeAccountChatDrafts")
    assert "submitButton.setAttribute('aria-busy', 'true')" in auth
    assert "form.setAttribute('aria-busy', 'true')" in auth
    assert "'Creating account…'" in auth and "'Logging in…'" in auth
    assert "showAuthError('Password must be at least 6 characters.', passwordInput)" in auth


def test_signed_out_shared_routes_keep_destination_and_explain_what_opens_next():
    assert 'id="auth-share-context" class="auth-share-context hidden" role="note"' in INDEX
    context = section("function renderSignedOutShareContext()", "const overlayRouteHash")
    for label in (
        "A court was shared with you",
        "A play session was shared with you",
        "A tournament was shared with you",
        "A Community was shared with you",
        "A private play group was shared with you",
        "A league was shared with you",
        "A conversation was shared with you",
    ):
        assert label in context
    assert "we’ll take you straight there" in context
    assert "fetch(`/api/share-preview?${params}`" in context
    assert "data-share-preview-title" in context
    assert "Details stay private" not in context
    assert "renderSignedOutShareContext();" in section("function logout(", "function tokenHint")
    boot = section("async function boot()", "boot().catch")
    assert boot.count("renderSignedOutShareContext();") >= 2


def test_new_account_setup_continues_on_dismissal_and_auto_checkin_is_contextual():
    watch = section("function startLocationWatch", "function stopLocationWatch")
    assert "Number(error?.code) !== 1" in watch
    assert "setAutoCheckInEnabled(false)" in watch
    assert "location access is blocked" in watch
    consent = section("function openAutoCheckInConsent", "const AUTO_CHECKIN_MILES")
    assert "{ onDismiss } = {}" in consent
    assert "modal._cleanupFns?.push" in consent
    assert "dismissModal(modal, onChange)" in consent
    assert "dismissModal(modal, onDismiss)" in consent
    chain = section("function runNewPlayerOnboarding", "async function showMain")
    assert "openPlayerBasicsOnboarding" in chain
    assert "openHomeAreaOnboarding" in chain
    assert "openAutoCheckInConsent" not in chain
    assert "maybeSuggestStarterCourts(afterCourts)" in chain
    assert "openOnboardingInvite(() => maybeShowTour" in chain
    assert "{ force: replay }" in chain
    assert "Number(state.me.id) !== accountId" in chain
    contextual = section("function maybeOfferAutoCheckInAfterManualCheckIn", "const AUTO_CHECKIN_MILES")
    assert "openAutoCheckInConsent" in contextual
    assert "`pp_auto_checkin_offer:${userId}`" in contextual
    assert "maybeOfferAutoCheckInAfterManualCheckIn(court)" in APP


def test_home_area_and_primary_court_have_distinct_jobs_without_quick_setup_alias():
    privacy = section("function openPrivacySafetySettings", "function openAppearanceSettings")
    assert 'id="privacy-home-area"' in privacy
    assert "Quick setup" not in privacy
    assert "privacy-replay-setup" not in privacy
    editor = section("function openEditProfile", "function gameFingerprint")
    assert "Home area controls nearby results" in editor
    assert "go-to venue for challenges and court shortcuts" in editor
    assert "<b>Primary court</b>" in editor


def test_incomplete_profile_stays_visible_on_play_until_four_useful_fields_are_saved():
    progress = section("function playerProfileSetupProgress", "function playerProfileSetupCardHtml")
    for key in ("key: 'level'", "key: 'availability'", "key: 'photo'", "key: 'court'"):
        assert key in progress
    card = section("function playerProfileSetupCardHtml", "async function completeNewPlayerOnboarding")
    assert "Complete your profile (${progress.complete} of ${progress.total})" in card
    assert "data-complete-player-setup" in card
    assert "progress.isComplete && state.me.onboarding_complete !== false" in card
    play = section("async function renderPlay", "function updatePlayHeader")
    assert "html += playerProfileSetupCardHtml();" in play
    assert "runNewPlayerOnboarding({ replay: true, profileOnly: true })" in play
    completion = section("async function completeNewPlayerOnboarding", "function openPlayerBasicsOnboarding")
    assert "if (!playerProfileSetupProgress().isComplete) return false;" in completion
    assert "profile_setup_incomplete" in APP
    assert "profile_setup_incomplete" in AUTH_BACKEND


def test_account_reads_are_event_driven_and_me_is_side_effect_free():
    heartbeat = section("state.presenceHeartbeatTimer = setInterval", "function slotForNow")
    assert "refreshMe();" not in heartbeat.split("if (tick % 3", 1)[0]
    assert "api('/presence/ping', { method: 'POST' })" in heartbeat
    assert "PRESENCE_HEARTBEAT_INTERVAL_MS" in heartbeat
    connectivity = section("function setupConnectivity()", "function setupServiceWorkerRouteMessages")
    assert "document.addEventListener('visibilitychange'" in connectivity
    assert "window.addEventListener('focus', refreshForegroundState)" in connectivity
    me_route = AUTH_BACKEND[AUTH_BACKEND.index("@auth_bp.get('/me')"):AUTH_BACKEND.index("@auth_bp.post('/auth/change-password')")]
    assert "return jsonify(_me_payload(g.current_user))" in me_route
    for old_job in ("maintain_expired_games", "maintain_tournament_results", "send_due_game_reminders"):
        assert old_job not in me_route


def test_first_paint_has_a_static_shell_and_non_parser_blocking_app_files():
    assert 'id="boot-screen" class="screen boot-screen"' in INDEX
    assert '<link rel="preload" href="/assets/r58/app-v15.min.js" as="script" />' in INDEX
    assert '<script defer src="/assets/r58/crew-planner-v15.min.js"></script>' in INDEX
    assert '<script defer src="/assets/r58/app-v15.min.js"></script>' in INDEX
    assert "if (!localStorage.getItem('pp_token'))" in INDEX
    assert "auth?.classList.remove('hidden')" in INDEX
    assert "window.__thirdShotShowBootFailure = showRecovery" in INDEX
    assert "'launch-timeout'" in INDEX and "'bundle-error'" in INDEX


def test_terms_and_privacy_are_available_before_signup_and_cover_core_data_use():
    assert 'data-auth-policy="terms"' in INDEX
    assert 'data-auth-policy="privacy"' in INDEX
    policy = section("function openAccountPolicy", "function openAccountActionDeepLink")
    for heading in (
        "Information we use",
        "Presence and sharing",
        "Your choices",
        "Security, retention, and contact",
        "Your account",
        "Playing and community conduct",
        "User content",
        "Real-world activities",
    ):
        assert heading in policy
    assert "does not sell your personal information" in policy
    assert "at least 13 years old" in policy
    assert "support@third-shot.app" in policy
