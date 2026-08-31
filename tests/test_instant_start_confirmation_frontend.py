"""Contracts for keeping an instant-rally start anchored at its launcher."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(start: str, end: str) -> str:
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_instant_start_confirms_at_the_trigger_before_showing_management():
    management = section(
        "function showInstantRallyManagement",
        "async function startInstantRally",
    )
    assert "result.outcome === 'joined'" in management
    assert "result.recovered" in management
    assert "game.is_creator" in management
    assert "Game started · Cancel" in management
    assert "confirmationTimer = setTimeout(revealManagement, 4000)" in management
    assert "button.textContent = 'Open game'" in management
    assert "openResolvedRallyGame(gameId, sourceModal)" in management
    assert "event.stopImmediatePropagation()" in management


def test_instant_start_cancel_uses_the_shared_confirmation_then_moves_under_more():
    management = section(
        "function showInstantRallyManagement",
        "async function startInstantRally",
    )
    assert "openGameCancellationConfirmation({" in management
    assert "variant: 'instant'" in management
    assert "api(`/games/${gameId}/cancel`" not in management
    assert "button.dataset.instantRallyAction = 'cancel'" in management
    assert "<summary>More</summary>" in management
    assert "Cancel game" in management
    assert "requestRallyCancellation(event.currentTarget)" in management
    assert "requestRallyCancellation(button)" in management
    assert "button.dataset.instantRallyAction = 'open'" in management
    assert "Game ended ✓" in management
    assert ".instant-rally-management { width: 100%; grid-column: 1 / -1; }" in STYLES


def test_success_only_opens_detail_when_no_visible_start_anchor_was_kept():
    continuation = section("function continueInstantRallyCall", "function finishInstantRallyCall")
    finish = section("function finishInstantRallyCall", "function rallyLauncherHtml")
    assert "{ ...options, confirmationButton: button }" in continuation
    assert "const confirmationButton = options.confirmationButton || null" in finish
    assert "const keptAnchor = showInstantRallyManagement(" in finish
    assert "options.openGame !== false && !keptAnchor" in finish


def test_check_in_start_hands_its_real_submit_button_to_the_confirmation():
    flow = section("async function checkInAndStartRally", "function openPlaySoonFlow")
    assert "confirmationButton: button" in flow
    assert "confirmationOriginalHtml: original" in flow
    assert "if (!button.dataset.instantRallyAction)" in flow


def test_joining_an_existing_rally_keeps_the_trigger_for_undo_before_opening_detail():
    join = section("async function openReadyRally", "function openCheckInSheet")
    assert "let keepConfirmation = false;" in join
    assert "button.dataset.rallyJoinUndo = 'true'" in join
    assert "button.textContent = 'Joined ✓ · Undo'" in join
    assert "api(`/games/${gameId}/leave`, { method: 'POST' })" in join
    assert "confirmationTimer = setTimeout(openJoinedGame, 4000)" in join
    assert "if (!keepConfirmation && button && document.body.contains(button))" in join
