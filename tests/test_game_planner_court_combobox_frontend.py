from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()


def planner_source():
    start = APP.index("async function openNewGameModal")
    end = APP.index("// ---------- Tournaments ----------", start)
    return APP[start:end]


def test_game_planner_uses_the_shared_accessible_court_combobox():
    planner = planner_source()
    assert 'id="ng-court-search"' in planner
    assert 'role="combobox" aria-autocomplete="list" aria-controls="ng-court-results"' in planner
    assert 'id="ng-court-results" role="listbox"' in planner
    assert "bindCourtComboboxNavigation(plannerCourtSearch, plannerCourtResults" in planner
    assert 'role="option" aria-selected="false" tabindex="-1"' in planner
    assert "plannerCourtNavigation.refresh();" in planner
    assert "plannerCourtNavigation.destroy();" in planner


def test_game_planner_court_search_recovers_without_losing_the_flow():
    planner = planner_source()
    assert "data-ng-court-search-retry" in planner
    assert "plannerCourtSearch.dispatchEvent(new Event('input', { bubbles: true }))" in planner
    assert "requestAnimationFrame(() => plannerCourtSearch.focus({ preventScroll: true }))" in planner
    assert "plannerCourtSearch.setAttribute('aria-expanded', 'false');" in planner
    assert "plannerCourtResults.removeAttribute('aria-busy');" in planner
