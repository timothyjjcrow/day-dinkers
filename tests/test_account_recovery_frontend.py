"""Source contracts for the account recovery and notification UI."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'public' / 'app-v15.js').read_text()
INDEX = (ROOT / 'public' / 'index.html').read_text()


def section(start, end):
    return APP[APP.index(start):APP.index(end, APP.index(start))]


def test_login_exposes_recovery_and_policy_destinations():
    assert 'id="auth-forgot-password"' in INDEX
    assert 'data-auth-policy="terms"' in INDEX
    assert 'data-auth-policy="privacy"' in INDEX
    setup = section('function setupAuth()', 'function showMain()')
    assert "$('#auth-forgot-password').addEventListener('click', openForgotPassword)" in setup
    assert "document.querySelectorAll('[data-auth-policy]')" in setup


def test_one_time_account_links_have_complete_browser_flows():
    recovery = section('function openForgotPassword()', 'function setupAuth()')
    assert "api('/auth/forgot-password'" in recovery
    assert "api('/auth/reset-password'" in recovery
    assert "api(changing ? '/auth/confirm-email-change' : '/auth/verify-email'" in recovery
    assert "location.hash.match(/^#(reset-password|verify-email|confirm-email)=" in recovery
    deep_links = section('function openDeepLink()', 'async function boot()')
    assert 'openAccountActionDeepLink()' in deep_links


def test_account_settings_exposes_email_and_session_security_controls():
    account = section('function openAccountSettings()', 'function openSettingsHub()')
    assert 'id="account-verify-email"' in account
    assert 'id="account-email-form"' in account
    assert "api('/auth/verify-email/request'" in account
    assert "api('/auth/change-email'" in account
    assert 'id="account-sessions-form"' in account
    assert "api('/auth/sessions/revoke-others'" in account
    assert 'persistReplacementToken(result)' in account


def test_direct_message_activity_opens_the_exact_thread():
    target = section('function notificationTarget', 'function openNotificationTarget')
    opener = section('function openNotificationTarget', 'function applyMe')
    assert "['direct_message', 'friend_request', 'friend_accept', 'friend_checkin', 'player_coming'].includes(n.kind)" in target
    assert "directMessage: n.kind === 'direct_message'" in target
    assert 'if (target.directMessage) openThread(Number(target.id));' in opener
    assert 'return openNotificationTarget(notification);' in APP
    assert "else if (route.kind === 'chat') openThread(route.id);" in APP
