"""Contracts for honest, contextual web-push opt-in prompts."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()


def section(start: str, end: str) -> str:
    begin = APP.index(start)
    return APP[begin : APP.index(end, begin)]


def test_contextual_prompt_waits_for_server_capability_without_burning_the_offer():
    prompt = section("const contextualPushPromptChecks", "async function boot")
    before_storage = prompt[: prompt.index("localStorage.setItem(key, '1')")]

    assert "async function maybeOfferPhoneNotifications" in prompt
    assert "const capability = await pushCapabilityState();" in before_storage
    assert "capability.configured !== true" in before_storage
    assert "!capability.supported" in before_storage
    assert "capability.permission !== 'default'" in before_storage
    assert "Notification.permission !== 'default'" in before_storage
    assert "catch(() => false)" in prompt


def test_contextual_prompt_is_user_and_token_scoped_and_deduplicated_in_flight():
    prompt = section("const contextualPushPromptChecks", "async function boot")

    assert "const contextualPushPromptChecks = new Map();" in prompt
    assert "contextualPushPromptChecks.has(userId)" in prompt
    assert "const requestOwner = captureAuthenticatedSessionOwner();" in prompt
    assert "!authenticatedSessionOwnerIsCurrent(requestOwner)" in prompt
    assert "contextualPushPromptChecks.delete(userId)" in prompt
    assert "localStorage.getItem(key) === '1'" in prompt


def test_permission_request_remains_inside_the_explicit_click_handler():
    prompt = section("const contextualPushPromptChecks", "async function boot")
    click_handler = section(
        "async function enablePhoneNotificationsFromPrompt",
        "const contextualPushPromptChecks",
    )

    assert "Notification.requestPermission" not in prompt
    assert "Notification.requestPermission" in click_handler
    assert "onClick: enablePhoneNotificationsFromPrompt" in prompt
    assert "if (permission === 'granted' && await syncPushSubscription())" in click_handler
