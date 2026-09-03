from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()


def test_broken_avatar_and_court_photos_reveal_product_fallbacks_without_inline_handlers():
    assert 'onerror="this.remove()"' not in APP
    assert APP.count("data-remove-on-error") >= 3
    assert "document.addEventListener('error', (event) =>" in APP
    assert "image instanceof HTMLImageElement" in APP
    assert "image.hasAttribute('data-remove-on-error')" in APP
    assert "image.remove();" in APP
