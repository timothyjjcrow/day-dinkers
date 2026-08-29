"""Focused source contracts for the streamlined court-discovery journey."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()


def section(start: str, end: str) -> str:
    start_at = APP.index(start)
    return APP[start_at:APP.index(end, start_at)]


def test_active_now_is_the_single_quick_activity_filter_with_advanced_fallbacks():
    setup = section("function setupMap()", "function locateMe")
    assert "quickFilters?.querySelector('[data-court-filter=\"players\"]')" in setup
    assert "legacyPlayers.dataset.courtFilter = 'active'" in setup
    assert "legacyPlayers.textContent = '🟢 Active now'" in setup
    assert "quickFilters?.querySelector('[data-court-filter=\"saved\"]')?.remove()" in setup
    assert "quickFilters?.querySelector('[data-court-filter=\"games\"]')?.remove()" in setup
    assert "$('#bell-btn')?.addEventListener" in setup
    assert "$('#court-sheet-expand')?.addEventListener" in setup

    filtering = section("function applyCourtFilters", "function addCourtDistances")
    assert "court.players_here > 0 || court.upcoming_games > 0" in filtering

    sheet = section("function openCourtFilterSheet", "async function fetchCourtsInView")
    assert "Saved courts" in sheet
    assert "Players here" in sheet
    assert "Open games" in sheet
    assert "modal.querySelectorAll('.court-filter-grid')" in sheet


def test_exact_court_matches_precede_places_and_list_cards_open_detail_directly():
    suggest = section("function renderSearchSuggest", "let searchSeq")
    assert suggest.index("🏓 Exact court") < suggest.index("📍 Jump to area")

    listing = section("function renderCourtList", "function openSuggestEditSheet")
    assert listing.index("🏓 Exact court") < listing.index("📍 Jump to area")
    assert "row.addEventListener('click', () => openCourtDetail(Number(row.dataset.court)))" in listing
    assert "row.addEventListener('click', () => selectCourtOnMap" not in listing

    markers = section("function drawMarkers", "function setCourtMarkerSelected")
    assert "on('click', () => selectCourtOnMap(court))" in markers


def test_typed_court_name_prefix_is_a_strong_match_before_places_everywhere():
    matching = section("function splitExactCourtNameMatches", "function renderSearchSuggest")
    assert "const queryName = String(q || '').trim().toLocaleLowerCase();" in matching
    assert "const courtName = String(court.name || '').trim().toLocaleLowerCase();" in matching
    assert "queryName && (courtName === queryName || courtName.startsWith(queryName))" in matching

    suggest = section("function renderSearchSuggest", "let searchSeq")
    assert "const { exact, other } = splitExactCourtNameMatches(courts, q);" in suggest
    assert suggest.index("courtRowsHtml(exact.slice(0, 2))") < suggest.index("places.slice(0, 4)")

    listing = section("function renderCourtList", "function openSuggestEditSheet")
    assert "? splitExactCourtNameMatches(courts, state.searchQ)" in listing
    assert "const displayCourts = [...searchMatches.exact, ...searchMatches.other];" in listing
    assert listing.index("visibleExact.map(courtRowHtml)") < listing.index("html += placesHtml")


def test_quiet_cards_hide_zero_metrics_and_zero_results_hide_sorting():
    card = section("function courtRowHtml", "function sortCourts")
    assert "if (c.players_here > 0)" in card
    assert "if (c.upcoming_games > 0)" in card
    assert "if (c.rating_count > 0 && c.rating_avg)" in card
    assert 'class="court-card-quiet">Quiet now' in card

    summary = section("function syncCourtSheetSummary", "function openCourtListPanel")
    assert "sort?.classList.toggle('hidden', n === 0)" in summary
    assert "sortLabel?.classList.toggle('hidden', n === 0)" in summary


def test_court_cards_expose_the_visible_decision_data_in_their_accessible_name():
    card = section("function courtRowHtml", "function sortCourts")
    assert "const accessibleActivity = quietNow ? 'Quiet now'" in card
    assert "const accessibleAmenities = [" in card
    assert "cond ? cond[1] : ''" in card
    assert "`${c.num_courts} court${c.num_courts === 1 ? '' : 's'}`" in card
    assert "`${c.rating_avg} stars from ${c.rating_count} rating" in card
    assert "const accessibleSummary = [" in card
    assert 'aria-label="${esc(accessibleSummary)}"' in card
    assert 'aria-label="View ${esc(c.name)}"' not in card


def test_court_detail_leads_with_now_and_defers_venue_management():
    detail = section("async function openCourtDetail", "function openCheckInSheet")
    now_at = detail.index('id="cd-now-heading">Now at this court')
    players = detail.index('id="cd-sec-players"')
    games = detail.index('id="cd-sec-games"')
    disclosure = detail.index('class="card cd-progressive cd-court-details"')
    assert now_at < players < games < disclosure
    assert "const nowGames = Array.isArray(court.now_games) ? court.now_games" in detail
    assert "const actionableGames = nowGames.filter((game) => game.is_joined || Number(game.spots_left) > 0);" in detail
    assert "const nGames = actionableGames.length;" in detail
    assert '<b>${nGames}</b><span>open game${nGames === 1 ? \'\' : \'s\'}</span>' in detail
    assert "const myOpenGame = nowGames.find((game) => game.is_joined && game.status === 'upcoming') || null;" in detail
    assert "const primaryAction = myOpenGame" in detail
    assert 'id="cd-open-game" data-game-id="${myOpenGame.id}">Open your game' in detail
    assert detail.index("const primaryAction = myOpenGame") < detail.index(": checkedIn")
    assert "Find or start a game" in detail
    assert "Get directions" in detail
    assert 'id="cd-checkout">Check out' in detail
    assert '<summary>Court details</summary>' in detail
    assert '<summary>More at this court</summary>' in detail
    assert 'class="card cd-progressive cd-reviews-details"' in detail
    assert "modal.querySelector('#cd-checkin')?.addEventListener" in detail
    assert "modal.querySelector('#cd-checkout')?.addEventListener" in detail
    assert "modal.querySelector('#cd-play-now')?.addEventListener" in detail


def test_court_address_copy_is_a_native_keyboard_accessible_button():
    detail = section("async function openCourtDetail", "function openCheckInSheet")
    assert '<button type="button" id="cd-address" class="cd-address-copy"' in detail
    assert 'aria-label="Copy court address: ${esc(courtAddressText)}"' in detail
    assert '<div id="cd-address" role="button"' not in detail
    assert "modal.querySelector('#cd-address').addEventListener('click'" in detail
    assert "navigator.clipboard.writeText(courtAddressText)" in detail


def test_single_markers_use_a_venue_symbol_while_clusters_keep_counts():
    marker = section("function courtMarkerIcon", "function drawMarkers")
    assert "${busy ? court.players_here + '👤' : '🏓'}" in marker
    clusters = section("function setupMap()", "function locateMe")
    assert "${n}</div>" in clusters
