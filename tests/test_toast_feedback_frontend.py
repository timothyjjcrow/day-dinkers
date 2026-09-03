from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
CSS = (ROOT / "public" / "styles-v15.css").read_text()


def test_toasts_use_product_icons_and_remove_functional_emoji_from_copy():
    assert "const TOAST_GLYPH_ICONS" in APP
    assert "function toastPresentation(message, options = {})" in APP
    assert "text = text.split(glyph).join(' ')" in APP
    assert "toast-icon" in APP
    assert "uiIcon(presentation.icon)" in APP
    assert "el.textContent = msg" not in APP


def test_toasts_have_semantic_visual_and_live_region_states():
    assert "toast is-${presentation.tone}" in APP
    assert "presentation.tone === 'error' || presentation.tone === 'warning'" in APP
    assert "el.setAttribute('role', isAlert ? 'alert' : 'status')" in APP
    assert "el.setAttribute('aria-live', isAlert ? 'assertive' : 'polite')" in APP
    assert ".toast.is-success" in CSS
    assert ".toast.is-warning" in CSS
    assert ".toast.is-error" in CSS
    assert ".toast.is-info" in CSS


def test_toasts_remain_readable_on_small_screens_and_long_messages():
    assert "max-width: min(calc(100vw - 28px), 430px)" in CSS
    assert "min-height: 48px" in CSS
    assert "const toastQueue = [];" in APP
    assert "while (activeToasts.size < 2 && toastQueue.length)" in APP
    assert "Math.max(6000, 3000 + presentation.text.length * 45)" in APP
    assert "Math.max(10000, 5000 + presentation.text.length * 55)" in APP
    assert "Math.max(8000, requestedVisibleFor)" in APP
    assert 'class="toast-dismiss" aria-label="Dismiss message"' in APP
    assert "action: { label: 'Open', onClick: () => openNotificationTarget(latest) }" in APP
    assert "overflow-wrap: anywhere" in CSS


def test_api_failures_have_an_explicit_error_toast_path():
    assert "function errorToast(error, options = {})" in APP
    assert "toast(message, { ...toastOptions, tone: 'error' })" in APP
    assert "const knownApiError = Object.values(ERROR_TEXT).includes(text)" in APP
    assert "const tone = knownApiError ? 'error'" in APP
    assert "const inferredError" in APP
    assert "options.tone === 'danger' ? 'error'" in APP
