import json
from pathlib import Path

import pytest

from backend.app import create_app, db
from backend.models import Court


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(source: str, start: str, end: str) -> str:
    start_at = source.index(start)
    return source[start_at : source.index(end, start_at)]


@pytest.fixture()
def client():
    app = create_app("testing")
    with app.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(Court(
            name="Decision Facts Courts",
            city="Irvine",
            state="CA",
            county_slug="orange-county",
            latitude=33.67,
            longitude=-117.82,
            num_courts=8,
            surface_type="Hard, Acrylic",
            court_type="dedicated",
            open_play_schedule="Saturday 8am–noon",
            fees="$5 drop-in",
            hours="Daily 6am–10pm",
            photo_url="https://images.example.test/court.jpg",
        ))
        db.session.commit()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def register(client):
    response = client.post("/api/auth/register", json={
        "email": "court-map@example.com",
        "password": "secret123",
        "display_name": "Map Player",
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def test_search_and_saved_court_summaries_include_decision_facts(client):
    listing = client.get("/api/courts?lat=33.67&lng=-117.82&radius=5").get_json()["items"]
    assert len(listing) == 1
    court = listing[0]
    expected = {
        "surface_type": "Hard, Acrylic",
        "court_type": "dedicated",
        "open_play_schedule": "Saturday 8am–noon",
        "fees": "$5 drop-in",
        "hours": "Daily 6am–10pm",
        "photo_url": "https://images.example.test/court.jpg",
    }
    assert {key: court[key] for key in expected} == expected

    account = register(client)
    headers = {"Authorization": f"Bearer {account['token']}"}
    favorite = client.put(
        f"/api/courts/{court['id']}/favorite",
        json={"favorited": True},
        headers=headers,
    )
    assert favorite.status_code == 200
    saved = client.get("/api/courts/favorites", headers=headers).get_json()["items"][0]
    assert {key: saved[key] for key in expected} == expected


def test_court_listing_cursor_reaches_every_result_and_announces_partial_map(client):
    with client.application.app_context():
        for index in range(4):
            db.session.add(Court(
                name=f"Cursor Court {index}", city="Irvine", state="CA",
                county_slug="orange-county", latitude=33.68 + index / 1000,
                longitude=-117.82, num_courts=index + 1,
            ))
        db.session.commit()

    first = client.get("/api/courts?bbox=-118,33,-117,34&limit=2&sort=courts")
    assert first.status_code == 200
    page = first.get_json()
    assert page["count"] == 2
    assert page["total"] == 5
    assert page["has_more"] is True
    assert page["next_cursor"]
    second = client.get(
        f"/api/courts?bbox=-118,33,-117,34&limit=2&sort=courts&cursor={page['next_cursor']}"
    ).get_json()
    assert second["count"] == 2
    assert {row["id"] for row in page["items"]}.isdisjoint(
        {row["id"] for row in second["items"]}
    )
    invalid = client.get("/api/courts?cursor=broken")
    assert invalid.status_code == 400
    assert invalid.get_json()["error"] == "invalid_cursor"

    assert "Zoom in for a complete local view, or load more courts." in APP
    assert 'id="court-load-more"' in APP
    assert "state.courtNextCursor = data.next_cursor || null" in APP


def test_open_now_filter_uses_structured_hours_before_pagination(client):
    always_open = {
        day: {'open': '00:00', 'close': '00:00'}
        for day in ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
    }
    always_open['timezone'] = 'America/Los_Angeles'
    with client.application.app_context():
        db.session.add(Court(
            name='Always Open Courts', city='Irvine', state='CA',
            latitude=33.671, longitude=-117.821,
            structured_hours=json.dumps(always_open),
        ))
        db.session.commit()

    response = client.get(
        '/api/courts?bbox=-118,33,-117,34&open_now=1&limit=1',
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['total'] == 1
    assert payload['has_more'] is False
    assert [court['name'] for court in payload['items']] == ['Always Open Courts']

    assert "['open_now', uiIcon('clock'), 'Open now']" in APP
    assert "COURT_DETAIL_FILTERS = ['open_now', 'business'" in APP
    assert "[...COURT_AMENITY_FILTERS, 'open_now']" in APP
    assert "court.open_status?.is_open !== true" in APP


def test_dark_mode_keeps_provider_cartography_and_themes_only_map_chrome():
    theme = section(APP, "function themeTileUrl", "function courtLocationAccuracyLabel")
    map_css = section(STYLES, "/* ---------- Map / Courts ---------- */", ".map-load-state {")

    assert "container.classList.toggle('map-chrome-dark', themeIsDark())" in theme
    assert "container.dataset.basemapTheme = 'daylight';" in theme
    assert "map-tiles-dark" not in APP
    assert "map-tiles-dark" not in map_css
    assert "filter:" not in map_css
    assert "#map.map-chrome-dark .leaflet-control-zoom a" in map_css
    assert "#map.map-chrome-dark .leaflet-control-attribution" in map_css


def test_granted_location_auto_centers_without_prompting_or_clobbering_interaction():
    location = section(APP, "function courtLocationAccuracyLabel", "function committedAreaLatLng")

    assert "navigator.permissions.query({ name: 'geolocation' })" in location
    assert "permission.state === 'granted'" in location
    assert "locateMe(true, { automatic: true })" in location
    assert "maximumAge: 60000" in location
    assert "state.selectedCourtId == null" in location
    assert "currentCenter.distanceTo(centerAtStart) < 25" in location
    assert "Turn on location to start with courts near you." in location
    assert "Allow it for Third Shot in browser settings" in location
    assert "if (!automatic) {" in location
    assert "state.areaLoc = null" in location
    assert "L.circle(state.userLoc" in APP
    assert "state.userAccuracyRing.setRadius(radius);" in APP
    assert "about ${Math.max(25, Math.round(feet / 25) * 25)} ft accuracy" in location


def test_court_cards_show_photos_and_only_supported_compact_facts():
    cards = section(APP, "function compactCourtFact", "function sortCourts")

    assert "function courtFeeFact" in cards
    assert "function courtTypeFact" in cards
    assert "'Lined / shared'" in cards
    assert 'class="court-card-photo' in cards
    assert 'loading="lazy" decoding="async"' in cards
    assert "data-remove-on-error" in cards
    assert 'class="court-card-photo-fallback"' in cards
    assert "hoursFact ? ['clock', hoursFact.label, hoursFact.raw" in cards
    assert "Open play: ${openPlayFact}" in cards
    assert "feeFact ? ['ticket', feeFact, c.fees]" in cards
    assert ".court-card-layout" in STYLES
    assert "grid-template-columns: 56px minmax(0, 1fr)" in STYLES
    assert ".court-card-facts" in STYLES


def test_court_text_search_ranks_prefixes_and_uses_conservative_typo_fallback(client):
    with client.application.app_context():
        db.session.add_all([
            Court(
                name='Pinecrest Pickleball', city='Irvine', state='CA',
                latitude=33.675, longitude=-117.82, num_courts=4,
            ),
            Court(
                name='Alpine Courts', city='Irvine', state='CA',
                latitude=33.671, longitude=-117.82, num_courts=8,
            ),
            Court(
                name='Community Courts', city='Pineville', state='CA',
                latitude=33.67, longitude=-117.82, num_courts=6,
            ),
        ])
        db.session.commit()

    ranked = client.get(
        '/api/courts?q=pine&lat=33.67&lng=-117.82',
    ).get_json()['items']
    assert [court['name'] for court in ranked] == [
        'Pinecrest Pickleball',
        'Community Courts',
        'Alpine Courts',
    ]

    typo = client.get('/api/courts?q=decison').get_json()['items']
    assert [court['name'] for court in typo] == ['Decision Facts Courts']
    # A vague, dissimilar query must stay empty rather than returning a
    # surprising fuzzy recommendation.
    assert client.get('/api/courts?q=zzzzzz').get_json()['items'] == []


def test_geocoder_biases_results_to_a_valid_local_viewbox(client, monkeypatch):
    import backend.routes.courts as courts_mod

    courts_mod._GEOCODE_CACHE.clear()
    captured = {}

    def fake_fetch(query, viewbox=None):
        captured.update(query=query, viewbox=viewbox)
        return [{
            'lat': '33.6846', 'lon': '-117.8265',
            'display_name': 'Irvine, Orange County, California, United States',
            'address': {'city': 'Irvine', 'state': 'California'},
        }]

    monkeypatch.setattr(courts_mod, '_nominatim_fetch', fake_fetch)
    response = client.get(
        '/api/geocode?q=Irvine&viewbox=-118.1000,34.1000,-117.5000,33.4000',
    )
    assert response.status_code == 200
    assert captured == {
        'query': 'Irvine',
        'viewbox': '-118.1000,34.1000,-117.5000,33.4000',
    }
    assert client.get(
        '/api/geocode?q=Irvine&viewbox=-118,33,-117,34',
    ).status_code == 400


def test_primary_court_search_is_quiet_local_and_useful_before_typing():
    setup = section(APP, 'function setupMap()', '// ---------- Theme')
    search = section(APP, 'function courtRecentSearchStorageKey', 'function courtMarkerIcon')
    focus_start = setup.index("searchInput.addEventListener('focus'")
    focus_end = setup.index("$('#search-clear').addEventListener", focus_start)
    focus_handler = setup[focus_start:focus_end]

    assert 'const COURT_SEARCH_DEBOUNCE_MS = 650;' in setup
    assert 'q.length < 3' in setup
    assert 'Type at least three characters' in setup
    assert 'renderCourtSearchStartChoices();' in setup
    assert 'state.courtSearchResultsQuery === state.searchQ' in focus_handler
    assert 'renderSearchSuggest(state.courtsInView, state.courtListPlaces, state.searchQ);' in focus_handler
    assert 'searchCourts(' not in focus_handler
    assert 'Use my location' in search
    assert 'Recent searches' in search
    assert 'Saved courts' in search
    assert 'pp_court_searches:${userId}' in search
    assert 'if (q.length < 3) return false;' in search
    assert 'courtGeocoderViewboxQuery()' in search
    assert 'bounds.getWest(), bounds.getNorth(), bounds.getEast(), bounds.getSouth()' in search
    assert '`/geocode?q=${encodeURIComponent(q)}${geocoderViewbox}`' in search


def test_context_strip_lives_in_sheet_and_desktop_search_uses_real_selector():
    index = (ROOT / 'public' / 'index.html').read_text()
    sheet_head = index[
        index.index('<div class="court-sheet-head"'):
        index.index('<div class="court-sheet-view-row"')
    ]
    assert 'class="court-context-strip"' in sheet_head
    assert 'id="presence-banner"' in sheet_head
    assert 'id="looking-banner"' in sheet_head
    assert 'id="use-map-area"' in sheet_head
    assert 'court-map-hud' not in index
    assert '.court-context-strip > :not(.hidden) ~ :not(.hidden)' in STYLES
    assert '#tab-courts .map-topbar {' in STYLES
    assert '#tab-courts .map-top {' not in STYLES
