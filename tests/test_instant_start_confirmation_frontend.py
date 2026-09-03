"""Contracts for immediate navigation after instant-rally mutations."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(start: str, end: str) -> str:
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_instant_start_has_no_timed_launcher_management_state():
    assert "function showInstantRallyManagement" not in APP
    assert "instantRallyAction" not in APP
    assert "confirmationTimer = setTimeout(revealManagement, 4000)" not in APP
    assert "started · Cancel" not in APP
    assert ".instant-rally-management" not in STYLES


def test_game_detail_owns_instant_game_cancellation_after_navigation():
    detail = section("function gameScreenHtml", "async function openGameScreen")
    handler = section(
        "box.querySelector('#gs-cancel')?.addEventListener",
        "box.querySelector('#gs-reschedule')?.addEventListener",
    )
    assert 'id="gs-cancel"' in detail
    assert "openGameCancellationConfirmation({" in handler
    assert "game.is_instant ? 'instant' : 'scheduled'" in handler


def test_every_successful_or_recovered_start_opens_detail_immediately():
    continuation = section("function continueInstantRallyCall", "function finishInstantRallyCall")
    finish = section("function finishInstantRallyCall", "function rallyLauncherHtml")
    assert "confirmationButton" not in continuation
    assert "showInstantRallyManagement" not in finish
    assert "if (gameId && options.openGame !== false)" in finish
    assert "openResolvedRallyGame(gameId, options.fromModal || null);" in finish


def test_check_in_start_does_not_leave_the_submit_button_as_a_second_screen():
    flow = section("async function checkInAndStartRally", "function openPlaySoonFlow")
    assert "confirmationButton" not in flow
    assert "confirmationOriginalHtml" not in flow
    assert "fromModal: modal" in flow


def test_joining_an_existing_rally_opens_detail_and_puts_undo_in_a_safe_toast():
    join = section("async function openReadyRally", "function openCheckInSheet")
    helper = section("function showJoinedToast", "function bindGameButtons")
    assert "keepConfirmation" not in join
    assert "Joined · Undo" not in join
    assert "showJoinedToast(gameId" in join
    assert "label: 'Undo'" in helper
    assert "api(`/games/${gameId}/leave`, { method: 'POST' })" in helper
    assert ".catch((error) =>" in helper
    assert "if (!error.isStaleSession) toast(error.message" in helper
    assert "openResolvedRallyGame(gameId, sourceModal || null);" in join
    assert "setTimeout(openJoinedGame, 4000)" not in join
