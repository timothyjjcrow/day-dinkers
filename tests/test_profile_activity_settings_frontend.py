"""Focused contracts for the Me, Activity, and notification-settings audit slice."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
CSS = (ROOT / "public" / "styles-v15.css").read_text()


def section(start: str, end: str) -> str:
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_profile_refresh_retains_content_scroll_and_retries_in_place():
    profile = section("async function renderProfile", "function openEditProfile")

    assert "const previousScrollTop = Math.max(0, Number(el.scrollTop) || 0);" in profile
    assert "el.dataset.profileReady === String(me.id)" in profile
    assert "el.classList.add('view-refreshing');" in profile
    assert "prefetchedDashboard = await dashboardRequest;" in profile
    assert "retainViewAfterError(" in profile
    assert "Showing your last update." in profile
    assert "el.dataset.profileReady = String(me.id);" in profile
    assert "el.scrollTop = previousScrollTop;" in profile


def test_new_player_milestones_are_visible_and_have_semantic_progress():
    profile = section("async function renderProfile", "function openEditProfile")

    assert 'id="pf-new-player-progress" aria-live="polite"' in profile
    assert "Number(stats.games_total || 0) === 0" in profile
    assert "stats.badge_progress || []" in profile
    assert "Your first milestones" in profile
    assert 'role="progressbar"' in profile
    assert 'aria-valuemax="${target}"' in profile
    assert 'aria-valuenow="${value}"' in profile
    assert ".profile-milestone-track" in CSS
    assert 'style="width:${progress}%"' in profile


def test_notification_settings_expose_honest_device_push_state():
    notifications = section("function openNotificationSettings", "async function loadBlockedPlayers")

    assert 'id="settings-device-push-toggle" role="switch"' in notifications
    assert 'aria-checked="false" disabled' in notifications
    assert "const iosNeedsInstall" in notifications
    assert "pushToggle.disabled = iosNeedsInstall" in notifications
    assert "Install from Safari first" in notifications
    assert "Not supported by this browser" in notifications
    assert "Not available from this server yet" in notifications
    assert "Blocked in browser settings" in notifications
    assert "revokePushSubscription(state.token)" in notifications
    assert "syncPushSubscription()" in notifications
    assert '.settings-switch-button[aria-checked="true"]' in CSS


def test_theme_picker_is_a_keyboard_operable_radiogroup():
    appearance = section("function openAppearanceSettings", "function openCalendarSettings")

    assert 'id="settings-theme" role="radiogroup"' in appearance
    assert 'type="button" role="radio" data-theme-pick=' in appearance
    assert 'aria-checked="${themePref() === theme}"' in appearance
    assert 'tabindex="${themePref() === theme ? \'0\' : \'-1\'}"' in appearance
    assert "['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End']" in appearance
    assert "item.setAttribute('aria-checked', String(active));" in appearance
    assert "item.tabIndex = active ? 0 : -1;" in appearance
    assert "themeButtons[next].focus();" in appearance
    assert "themeButtons[next].click();" in appearance


def test_activity_has_cursor_pagination_filters_and_inline_decisions():
    activity = section("async function openActivity", "function renderPresenceBanner")

    assert "api('/notifications?limit=20')" in activity
    assert "before_id=${encodeURIComponent(requestedCursor)}" in activity
    assert "page.next_cursor || null" in activity
    assert "nextCursor !== requestedCursor" in activity
    assert 'role="radiogroup" aria-label="Filter activity"' in activity
    for value in ('all', 'games', 'people', 'groups'):
        assert f"['{value}'," in activity
    assert "['ArrowLeft', 'ArrowRight', 'Home', 'End']" in activity
    for action in (
        'friend-accept', 'friend-decline', 'crew-accept', 'crew-decline',
        'club-accept', 'club-decline', 'tournament-accept',
        'tournament-decline', 'game-accept', 'game-decline',
        'score-confirm', 'score-dispute',
    ):
        assert action in activity
    assert "openActionConfirmation({" in activity
    assert "data-activity-read-visible" in activity
    assert "data-activity-clear-all" in activity
    assert "data-activity-clear=\"${notification.id}\"" in activity
    assert "action: { label: 'Undo', onClick: restore }" in activity
