from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
CSS = (ROOT / "public" / "styles-v15.css").read_text()


def test_business_preview_scroll_respects_reduced_motion():
    start = APP.index("function openBusinessPlayerPreview")
    end = APP.index("function businessUnavailableHtml", start)
    preview = APP[start:end]
    assert "window.matchMedia?.('(prefers-reduced-motion: reduce)').matches" in preview
    assert "behavior: reduceMotion ? 'auto' : 'smooth'" in preview


def test_css_has_a_global_reduced_motion_safety_net():
    assert "@media (prefers-reduced-motion: reduce)" in CSS
    assert "animation-duration: .01ms !important" in CSS
    assert "transition-duration: .01ms !important" in CSS
