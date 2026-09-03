from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()


def source():
    start = APP.index("async function openLogGameSheet")
    end = APP.index("async function openNewGameModal", start)
    return APP[start:end]


def test_past_game_court_search_uses_the_shared_accessible_combobox():
    log_game = source()
    assert 'role="combobox"' in log_game
    assert 'aria-autocomplete="list"' in log_game
    assert 'role="listbox"' in log_game
    assert "bindCourtComboboxNavigation(logCourtSearch, logCourtResults" in log_game
    assert 'role="option" aria-selected="false" tabindex="-1"' in log_game
    assert "logCourtNavigation.refresh();" in log_game
    assert "logCourtNavigation.destroy();" in log_game


def test_past_game_court_search_has_retry_and_selection_focus_restoration():
    log_game = source()
    assert "data-lg-court-search-retry" in log_game
    assert "logCourtSearch.dispatchEvent(new Event('input', { bubbles: true }))" in log_game
    assert "logCourtResults.hidden = true;" in log_game
    assert "logCourtSearch.setAttribute('aria-expanded', 'false');" in log_game
    assert "logCourtSearch.focus({ preventScroll: true })" in log_game


def test_past_game_dropdowns_provide_product_titles_and_context_prefixes():
    log_game = source()
    assert 'data-select-title="Your partner" data-select-prefix="Partner"' in log_game
    assert 'data-select-title="Opponent" data-select-prefix="Opponent"' in log_game
    assert 'data-select-title="Second opponent" data-select-prefix="Opponent 2"' in log_game
    assert 'data-select-title="Court" data-select-prefix="Court"' in log_game
