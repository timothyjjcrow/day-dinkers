"""Contracts for the app-native whole-game cancellation experience."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(start: str, end: str, source: str = APP) -> str:
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


def test_cancellation_is_a_guarded_contextual_sheet_with_inline_feedback():
    confirmation = section(
        "function openGameCancellationConfirmation",
        "// Why a joinable game suits this player",
    )
    assert "openModal(`" in confirmation
    assert 'class="game-cancel-summary"' in confirmation
    assert "courtName" in confirmation
    assert "fmtDateTime(game.scheduled_at)" in confirmation
    assert "const format =" in confirmation
    assert 'class="game-cancel-impact"' in confirmation
    assert "What happens next" in confirmation
    assert 'data-game-cancel-keep' in confirmation
    assert 'data-game-cancel-confirm' in confirmation
    assert 'role="alert"' in confirmation
    assert 'role="status" aria-live="polite"' in confirmation
    assert "content.removeAttribute('aria-busy')" in confirmation
    assert "sheet._dismissBlocked = () => committing" in confirmation
    assert "sheet._returnFocus = trigger" in confirmation
    assert "sheet._returnFocusFallback = () => document.querySelector('#bottom-nav .nav-btn[aria-current=\"page\"]')" in confirmation
    assert "confirmButton.setAttribute('aria-busy', 'true')" in confirmation
    assert "keep.disabled = true" in confirmation
    assert "closeButton.disabled = true" in confirmation
    assert confirmation.count("/cancel`") == 1
    assert "confirm(" not in confirmation
    assert "toast(" not in confirmation


def test_cancel_focus_falls_back_to_the_current_tab_after_a_card_refresh():
    focus_restore = section("function focusAfterModalChange", "function destroyModal")
    assert "removed?._returnFocusFallback" in focus_restore
    assert "preferred && preferred.isConnected" in focus_restore
    assert "? preferred : fallback" in focus_restore


def test_all_whole_game_cancel_entry_points_delegate_to_the_sheet():
    stale = section(
        "rootEl.querySelectorAll('[data-game-dismiss]')",
        "rootEl.querySelectorAll('[data-game-dispute]')",
    )
    assert "openGameCancellationConfirmation({" in stale
    assert "variant: 'stale'" in stale
    assert "confirm(" not in stale
    assert "/cancel" not in stale

    instant = section(
        "function showInstantRallyManagement",
        "async function startInstantRally",
    )
    assert "openGameCancellationConfirmation({" in instant
    assert "variant: 'instant'" in instant
    assert "requestRallyCancellation(event.currentTarget)" in instant
    assert "requestRallyCancellation(button)" in instant
    assert "confirm(" not in instant
    assert "/cancel" not in instant
    assert "toast('Game cancelled')" not in instant

    detail = section(
        "box.querySelector('#gs-cancel')?.addEventListener",
        "box.querySelector('#gs-reschedule')?.addEventListener",
    )
    assert "openGameCancellationConfirmation({" in detail
    assert "game.is_instant ? 'instant' : 'scheduled'" in detail
    assert "render(fresh)" in detail
    assert "confirm(" not in detail
    assert "/cancel" not in detail
    assert "toast(" not in detail


def test_declining_a_challenge_uses_the_same_designed_confirmation():
    decline = section(
        "box.querySelector('#gs-decline')?.addEventListener",
        "box.querySelector('#gs-score')?.addEventListener",
    )
    assert "openGameCancellationConfirmation({" in decline
    assert "variant: 'challenge'" in decline
    assert "endpoint: `/games/${gameId}/decline`" in decline
    assert "confirm(" not in decline
    assert "toast(" not in decline


def test_cancellation_sheet_has_a_distinct_destructive_surface_and_mobile_actions():
    for selector in (
        ".game-cancel-backdrop",
        ".game-cancel-hero",
        ".game-cancel-summary",
        ".game-cancel-impact",
        ".game-cancel-actions",
        ".game-cancel-danger",
        ".game-cancel-success",
    ):
        assert selector in STYLES
    assert ".game-cancel-danger:focus-visible" in STYLES
    assert "min-height: 50px" in STYLES
    assert "@media (max-width: 360px)" in STYLES
    assert ".game-cancel-actions { grid-template-columns: 1fr; }" in STYLES
    assert ".game-cancel-actions [data-game-cancel-confirm] { grid-row: 1; }" not in STYLES


def test_game_detail_poll_cannot_overwrite_a_cancelled_parent_under_the_sheet():
    polling = section(
        "// Live sync: while this screen is open",
        "function safeNotificationOverlayRoute",
    )
    after_request = polling.index("const fresh = await api(`/games/${gameId}`);")
    ownership_check = polling.index(
        "if (!document.body.contains(box) || currentOverlayEntry()?.el !== modal) return;"
    )
    render_check = polling.index("if (gameFingerprint(fresh) !== fingerprint)")
    assert after_request < ownership_check < render_check
