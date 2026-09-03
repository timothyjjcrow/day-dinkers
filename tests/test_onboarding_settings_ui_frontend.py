from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
INDEX = (ROOT / "public" / "index.html").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(start: str, end: str) -> str:
    return APP.split(start, 1)[1].split(end, 1)[0]


def css(selector: str) -> str:
    return STYLES.split(f"{selector} {{", 1)[1].split("}", 1)[0]


def test_auth_has_visible_hierarchy_labeled_controls_and_password_reveal():
    assert 'aria-labelledby="auth-title"' in INDEX
    assert 'class="auth-eyebrow" id="auth-eyebrow">Welcome back' in INDEX
    assert '<h1 id="auth-title">Log in to Third Shot</h1>' in INDEX
    assert '<form id="auth-form" novalidate aria-describedby="auth-support">' in INDEX
    for control in ("auth-name", "auth-email", "auth-password"):
        assert f'<label for="{control}">' in INDEX
    assert 'class="auth-control auth-password-control"' in INDEX
    assert 'id="auth-password-toggle" aria-label="Show password" aria-pressed="false"' in INDEX
    assert '<use href="#ui-eye" />' in INDEX

    auth = section("function setupAuth", "function purgeAccountChatDrafts")
    assert "showAuthError('Enter the name players should see.', nameInput)" in auth
    assert "showAuthError('Enter your email address.', emailInput)" in auth
    assert "showAuthError('Enter a complete email address.', emailInput)" in auth
    assert "showAuthError('Password must be at least 6 characters.', passwordInput)" in auth
    assert "target.setAttribute('aria-invalid', 'true')" in auth
    assert "submitButton.setAttribute('aria-busy', 'true')" in auth
    assert "form.setAttribute('aria-busy', 'true')" in auth
    assert "'Creating account…'" in auth and "'Logging in…'" in auth and "'Verifying…'" in auth
    assert "passwordInput.type = showing ? 'password' : 'text';" in auth
    assert "button.setAttribute('aria-pressed', String(!showing));" in auth

    assert "min-height: 52px" in css(".auth-control > input")
    assert "min-height: var(--tap-min)" in css(".auth-password-toggle")


def test_new_player_onboarding_is_connected_and_uses_product_icons():
    auth = section("function setupAuth", "function purgeAccountChatDrafts")
    assert "if (openedLinkedDestination) pendingNewPlayerOnboardingAccountId = state.me.id;" in auth
    assert "else runNewPlayerOnboarding();" in auth

    home = section("function openHomeAreaSheet", "// Onboarding step 2")
    assert "checkin-sheet-icon home-area-hero" in home
    assert "uiIcon('map-pin')" in home
    assert "uiIcon('target')" in home
    assert '<label for="ha-city">Search by city</label>' in home
    assert "onboarding-privacy-note" in home
    assert "uiIcon('shield')" in home
    assert "status.setAttribute('role', 'alert')" in home

    starter = section("async function maybeSuggestStarterCourts", "function homeAreaOnboardingKey")
    assert "uiIcon('star')" in starter
    assert "uiIcon('map-pin')" in starter
    assert 'type="button" class="btn ${saved' in starter
    assert "Rated ${c.rating_avg}" in starter
    assert 'label: \'Save nearby courts\'' in starter
    assert 'data-home-court="${c.id}"' in starter
    assert "'Primary' : 'Set primary'" in starter

    tour = section("function maybeShowTour", "function openPlayerBasicsOnboarding")
    assert "const tourKey = `pp_tour_seen:${state.me.id}`;" in tour
    assert "{ icon: 'map'" in tour
    assert "{ icon: 'users'" in tour
    assert "{ icon: 'pickleball'" in tour
    assert "uiIcon(s.icon)" in tour
    assert 'role="status">Step ${i + 1} of ${steps.length}' in tour
    assert 'type="button" class="btn btn-primary btn-block onboarding-tour-next"' in tour
    assert "🗺️" not in tour and "🤝" not in tour and "🏓" not in tour

    basics = section("function openPlayerBasicsOnboarding", "function runNewPlayerOnboarding")
    assert "Profile step 1 of 3" in basics
    assert "Profile step 2 of 3" in basics
    assert "Profile step 3 of 3" in basics
    assert "What’s your level?" in basics
    assert "When do you usually play?" in basics
    assert "Add a profile photo" in basics
    assert 'role="radiogroup" aria-label="Pickleball self-rating"' in basics
    assert "SELF_RATING_CHOICES.map" in basics
    assert 'data-onboarding-availability="${slot}"' in basics
    assert "skill_rating: rating" in basics
    assert "const availability = [...modal.querySelectorAll('[data-onboarding-availability].active')]" in basics
    assert 'id="onboarding-photo-file" accept="image/jpeg,image/png,image/webp"' in basics
    assert "imageFileToDataUrl(file, 768, { square: true })" in basics
    assert "JSON.stringify({ avatar_data: photoData || null })" in basics
    assert "photoRemoved" in basics
    assert basics.count("Skip for now") == 3

    chain = section("function runNewPlayerOnboarding", "async function showMain")
    assert "openPlayerBasicsOnboarding(() =>" in chain
    assert "openHomeAreaOnboarding({ replay, onComplete:" in chain
    assert "maybeSuggestStarterCourts(afterCourts)" in chain
    assert "openOnboardingInvite(() => maybeShowTour" in chain
    assert "maybeShowTour(" in chain
    assert "{ force: replay }" in chain
    assert "completeNewPlayerOnboarding(accountId" in chain


def test_settings_hub_groups_destinations_with_shared_icons():
    hub = section("function openSettingsHub", "async function renderProfile")
    for heading in (
        "Your player profile",
        "Preferences",
        "For pickleball businesses",
        "Account access",
    ):
        assert f"['{heading}'" in hub
    assert 'class="settings-destination-group" aria-labelledby="settings-group-${groupIndex}"' in hub
    assert 'class="settings-destination-icon" aria-hidden="true">${uiIcon(icon)}' in hub
    assert 'data-settings-destination="${key}"' in hub
    assert "openChildModal(" in hub

    assert "min-height: 66px" in css(".settings-destination")
    assert "width: 36px" in css(".settings-destination-icon")


def test_notification_preferences_are_grouped_accessible_product_switches():
    notifications = section("function openNotificationSettings", "async function loadBlockedPlayers")
    for group in ("['Games'", "['Messages'", "['Digests'"):
        assert group in notifications
    assert "label: 'Game chats'" in notifications
    assert "label: 'Group chats'" in notifications
    assert "Weekly session re-RSVP reminders" not in notifications
    assert "row.kinds.filter((kind) => availableKinds.has(kind))" in notifications
    assert "uiIcon(icon)" in notifications
    assert 'type="checkbox" role="switch" class="settings-notification-toggle"' in notifications
    assert 'data-kinds="${esc(kinds.join(\',\'))}"' in notifications
    assert "data-notification-state" in notifications
    assert "stateCopy.textContent = toggle.checked ? 'On' : 'Off';" in notifications
    assert "fieldset.setAttribute('aria-busy', 'true')" in notifications
    assert "Your previous choices were restored." in notifications
    assert "renderedKinds" in notifications

    toggle = css("input.settings-notification-toggle")
    assert "width: 48px" in toggle
    assert "height: 44px" in toggle
    assert "appearance: none" in toggle
    assert "input.settings-notification-toggle::before" in STYLES
    assert "border-radius: var(--radius-pill)" in css("input.settings-notification-toggle::before")
    assert "input.settings-notification-toggle:checked::after" in STYLES


def test_privacy_controls_have_switch_state_and_recoverable_block_management():
    blocked = section("async function loadBlockedPlayers", "function openPrivacySafetySettings")
    assert "box.setAttribute('aria-busy', 'true')" in blocked
    assert "box.removeAttribute('aria-busy')" in blocked
    assert 'aria-label="Unblock ${esc(user.display_name)}"' in blocked
    assert "beginButtonAction(button, 'Unblocking…')" in blocked
    assert "showInlineActionError(row, error.message)" in blocked
    assert "settings-empty-state" in blocked
    assert "uiIcon('shield')" in blocked

    privacy = section("function openPrivacySafetySettings", "function openAppearanceSettings")
    assert "settings-leaf-intro" in privacy
    assert 'class="settings-switch-button" id="privacy-auto-checkin"' in privacy
    assert 'aria-label="Auto check-in: ${checkInEnabled ? \'On\' : \'Off\'}"' in privacy
    assert "toggle.setAttribute('aria-label', `Auto check-in: ${enabled ? 'On' : 'Off'}`);" in privacy
    assert 'data-blocked-players aria-live="polite"' in privacy
    assert "Loading blocked players…" in privacy

    switch = css(".settings-switch-button")
    assert "min-height: var(--tap-min)" in switch
    assert "min-width: 76px" in switch


def test_account_security_uses_visible_labels_inline_success_and_confirmation():
    account = section("function openAccountSettings", "function openSettingsHub")
    assert "settings-account-summary-icon" in account
    assert "uiIcon('key')" in account
    assert "uiIcon('trash')" in account
    for control, label in (
        ("account-current-password", "Current password"),
        ("account-new-password", "New password"),
        ("account-delete-password", "Confirm your password"),
    ):
        assert f'<label for="{control}">{label}</label>' in account
    assert 'id="account-password-status" role="status" aria-live="polite"' in account
    assert "status.classList.remove('hidden')" in account
    assert "title: 'Log out of Third Shot?'" in account
    assert "tone: 'primary'" in account
    assert 'openAccountDeletionConfirmation(impact, button)' in account
    assert 'Delete your account forever?' in APP
    assert "bindModalDiscardConfirmation(modal" in account

    assert "min-height: 64px" in css(".settings-account-section summary")
    assert "min-height: 48px" in css(".account-form-action, .account-logout")


def test_profile_editor_has_scannable_sections_and_responsive_day_targets():
    editor = section("function openEditProfile", "function gameFingerprint")
    for title in ("About you", "Usually plays", "Primary court"):
        assert f"<b>{title}</b>" in editor
    assert "profile-editor-section-heading" in editor
    assert "profile-availability-period" in editor
    assert "uiIcon(availabilityIcons[part])" in editor
    assert 'aria-label="${partLabel}, ${availabilityDayLabels[day]}"' in editor
    assert "🌅" not in editor and "☀️" not in editor and "🌆" not in editor
    assert 'role="combobox" aria-autocomplete="list"' in editor
    assert 'aria-controls="ep-court-results" aria-expanded="false"' in editor
    assert 'role="option" aria-selected="false"' in editor
    assert "event.key === 'ArrowDown'" in editor
    assert "event.key === 'Escape'" in editor
    assert 'id="ep-avatar-file" accept="image/jpeg,image/png,image/webp"' in editor
    assert "imageFileToDataUrl(file, 768, { square: true })" in editor
    assert "if (pendingAvatarData) body.avatar_data = pendingAvatarData;" in editor
    assert "Take or choose photo" in editor
    assert "ep-bio-count" in editor
    assert "bioInput.addEventListener('input', syncBioCount)" in editor
    assert "Save profile changes" in editor

    days = css(".profile-availability-days")
    chip = css(".profile-availability-days .av-chip")
    assert "repeat(7, minmax(0, 1fr))" in days
    assert "width: 100%" in chip
    assert "@media (max-width: 420px)" in STYLES
    narrow = STYLES.split("@media (max-width: 420px)", 1)[1].split("}", 3)
    assert any("repeat(4, minmax(44px, 1fr))" in rule for rule in narrow)
