from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "public" / "index.html").read_text()
APP = (ROOT / "public" / "app-v15.js").read_text()


def test_play_feed_is_not_one_large_live_region():
    assert '<div id="play-content" class="tab-scroll" role="tabpanel"' in INDEX
    assert 'id="play-content" class="tab-scroll" aria-live=' not in INDEX


def test_play_view_uses_a_small_atomic_status_region():
    assert 'id="play-view-status" class="sr-only" role="status" aria-live="polite" aria-atomic="true"' in INDEX
    assert "viewStatus.dataset.segment !== seg" in APP
    assert "scores: 'Rankings loaded.'" in APP
