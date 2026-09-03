"""Contracts that keep the Courts viewport separate from personal discovery."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()


def section(start: str, end: str) -> str:
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_personal_origin_is_nullable_and_never_inherits_the_map_seed():
    origin = section("function committedAreaLatLng", "function areaViewKey")
    committed = origin[:origin.index("function courtDistanceOrigin")]
    assert "return null;" in origin
    assert "DEFAULT_CENTER" not in committed
    assert "state.map.getCenter()" in origin  # only courtDistanceOrigin
    assert "state.map" not in committed
    assert "function areaLatLng" not in APP


def test_hard_nearby_surfaces_stop_before_requesting_without_an_area():
    arrivals = section("async function openPlaySoonArrivalChoices", "async function openPlayNowCourtPicker")
    people = section("async function renderNearbyPlayers", "async function renderFriends")
    for source, endpoint in (
        (arrivals, "/players/looking?lat="),
        (people, "/players/nearby?lat="),
    ):
        assert "const loc = committedAreaLatLng();" in source
        assert source.index("if (!loc)") < source.index(endpoint)
        assert "Set your area" in source


def test_optional_court_suggestions_skip_nearby_but_keep_other_sources():
    planner = section("async function openNewGameModal", "async function renderTournaments")
    tournament = section("async function openCreateTournamentSheet", "async function openTournamentScreen")
    for source in (planner, tournament):
        assert "const c = committedAreaLatLng();" in source
        assert "? api(`/courts?lat=${c.lat}&lng=${c.lng}" in source
        assert ": Promise.resolve({ items: [] })" in source
    assert "api('/courts/favorites')" in planner
    assert "api('/clubs/mine')" in planner


def test_friends_results_need_no_location_and_rankings_explain_the_fallback():
    friends = section("async function renderFriends", "async function openThread")
    scores = section("if (seg === 'scores')", "// --- Games:")
    assert "api('/games/results')" in friends
    assert "lat=${" not in friends
    assert "Set your area for local rankings" in scores
    assert "btn.dataset.scope === 'near' && !committedAreaLatLng()" in scores


def test_competitions_do_not_claim_an_empty_local_area_when_discovery_was_skipped():
    competitions = section("async function renderTournaments", "// ---------- Shared competition results")
    assert "areaUnset: true" in competitions
    assert "Set your area for nearby competitions" in competitions
    assert "!nearbyResult.areaUnset" in competitions
