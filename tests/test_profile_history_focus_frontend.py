from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()


def test_profile_history_filter_restores_focus_after_its_results_rerender():
    start = APP.index("// Saved courts (primary court first)")
    end = APP.index("function openEditProfile", start)
    profile = APP[start:end]
    assert "const render = ({ restoreFilterFocus = false } = {})" in profile
    assert "render({ restoreFilterFocus: true });" in profile
    assert 'querySelector(`[data-hf="${active}"]`)' in profile
    assert "activeFilter.focus({ preventScroll: true })" in profile
