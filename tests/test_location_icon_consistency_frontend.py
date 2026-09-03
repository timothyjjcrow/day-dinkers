from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
CSS = (ROOT / "public" / "styles-v15.css").read_text()


def section(start, end):
    begin = APP.index(start)
    return APP[begin:APP.index(end, begin)]


def test_auto_checkin_consent_uses_the_product_location_icon():
    consent = section("function openAutoCheckInConsent", "const AUTO_CHECKIN_MILES")
    assert 'class="consent-hero" aria-hidden="true">${uiIcon(\'map-pin\')}' in consent
    assert "📍" not in consent
    assert ".consent-hero .ui-icon" in CSS


def test_nearby_players_empty_state_has_structured_product_ui():
    nearby = section("async function renderNearbyPlayers", "async function renderFriends")
    assert 'class="empty-state community-nearby-empty"' in nearby
    assert 'class="empty-state-icon" aria-hidden="true">${uiIcon(\'map-pin\')}' in nearby
    assert 'class="empty-state-copy">Check in at a court so others can find you.' in nearby
    assert '<span class="big">📍</span>' not in nearby
