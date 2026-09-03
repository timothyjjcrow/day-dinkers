"""Regression contracts for the final Courts filtering and load-state pass."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
INDEX = (ROOT / "public" / "index.html").read_text()


def section(start: str, end: str) -> str:
    start_at = APP.index(start)
    return APP[start_at:APP.index(end, start_at)]


def test_happening_now_forces_then_restores_sort_without_empty_result_toast():
    assert 'data-court-filter="active" aria-pressed="false"' in INDEX
    assert '> Happening now</button>' in INDEX

    sorting = section(
        "function syncActiveCourtSort()", "function syncCourtFilterControls()",
    )
    assert "const allowed = new Set(['distance', 'rating', 'courts']);" in sorting
    assert "if (state.listSort !== 'active')" in sorting
    assert "state.courtSortBeforeActive = allowed.has(state.listSort)" in sorting
    assert "state.listSort = 'active';" in sorting
    assert "else if (state.listSort === 'active')" in sorting
    assert "state.listSort = allowed.has(state.courtSortBeforeActive)" in sorting
    assert "? state.courtSortBeforeActive : 'distance';" in sorting
    assert "if (sort && !state.courtFilters.active) sort.value = state.listSort;" in sorting

    controls = section(
        "function syncCourtFilterControls()", "function courtAmenityQuery()",
    )
    assert "syncActiveCourtSort();" in controls

    setup = section("function setupMap()", "// ---------- Theme ----------")
    click_start = setup.index("quickFilters?.addEventListener('click'")
    click_end = setup.index("$('#court-more-filters')", click_start)
    click_handler = setup[click_start:click_end]
    assert "state.courtFilters[key] = !state.courtFilters[key];" in click_handler
    assert "syncCourtFilterControls();" in click_handler
    assert "await refreshCourtResults({ label: 'Applying court filters…' });" in click_handler
    assert "toast(" not in click_handler
    assert "No courts" not in click_handler

    listing = section("function renderCourtList", "function openSuggestEditSheet")
    assert 'class="court-result-empty" role="status"' in listing
    assert "No courts match these filters" in listing


def test_first_map_load_retains_one_loader_through_assets_and_court_fetch():
    bootstrap = section("async function ensureMapReady()", "function moveCourtMapWithoutRefresh")
    assert bootstrap.count("beginCourtContextRefresh(") == 1
    assert "beginCourtContextRefresh('Opening the court finder…');" in bootstrap
    assert "mapInitialRefreshPending = true;" in bootstrap
    assert "initialCourtFetch = setupMap();" in bootstrap
    assert "mapInitialRefreshPending = false;" in bootstrap
    assert "await initialCourtFetch;" in bootstrap
    assert "mapEl.setAttribute('aria-busy', 'false');" in bootstrap
    assert (
        bootstrap.index("ensureMapAssets().then")
        < bootstrap.index("mapInitialRefreshPending = true;")
        < bootstrap.index("initialCourtFetch = setupMap();")
        < bootstrap.index("await initialCourtFetch;")
        < bootstrap.index("mapEl.setAttribute('aria-busy', 'false');")
    )

    setup = section("function setupMap()", "// ---------- Theme ----------")
    assert "const initialRefreshPending = mapInitialRefreshPending;" in setup
    assert "if (!initialRefreshPending) beginCourtContextRefresh();" in setup
    assert "const initialCourtFetch = fetchCourtsInView({ surfaceError: true });" in setup
    assert "return initialCourtFetch;" in setup
