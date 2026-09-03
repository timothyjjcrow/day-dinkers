from pathlib import Path

import pytest

from backend.app import create_app, db
from backend.models import CheckIn, Court


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "public" / "app-v15.js").read_text()
STYLES = (ROOT / "public" / "styles-v15.css").read_text()


def section(source: str, start: str, end: str) -> str:
    start_at = source.index(start)
    return source[start_at : source.index(end, start_at)]


@pytest.fixture()
def closed_court_client():
    app = create_app("testing")
    with app.app_context():
        db.drop_all()
        db.create_all()
        court = Court(
            name="Closed Detail Court",
            city="Irvine",
            state="CA",
            county_slug="orange-county",
            latitude=33.68,
            longitude=-117.82,
            num_courts=4,
            closed=True,
        )
        db.session.add(court)
        db.session.commit()
        yield app, app.test_client(), court.id
        db.session.remove()
        db.drop_all()


def test_closed_court_checkin_is_rejected_without_creating_presence(closed_court_client):
    app, client, court_id = closed_court_client
    account = client.post("/api/auth/register", json={
        "email": "closed-court@example.com",
        "password": "secret123",
        "display_name": "Closed Court Player",
    }).get_json()

    response = client.post(
        f"/api/courts/{court_id}/checkin",
        json={"looking_for_game": True},
        headers={"Authorization": f"Bearer {account['token']}"},
    )

    assert response.status_code == 409
    assert response.get_json() == {"error": "court_closed"}
    with app.app_context():
        assert CheckIn.query.filter_by(
            user_id=account["user"]["id"], court_id=court_id,
        ).count() == 0


def test_closed_detail_replaces_every_new_play_surface_but_keeps_exit_and_correction():
    detail = section(APP, "async function openCourtDetail", "function openCheckInSheet")

    assert "if (court.closed === true)" in detail
    assert "Player-organized sessions are paused while this court is marked closed." in detail
    assert "const primaryAction = courtClosed ? ''" in detail
    assert "const secondaryActions = courtClosed ?" in detail
    assert "courtClosed ? '' : myOpenGame" in detail
    assert "courtClosed || venueBusiness ? ''" in detail
    assert "p.is_me || court.closed ? ''" in detail
    assert "const presenceControl = checkedIn ?" in detail
    assert 'id="cd-checkout">Check out</button>' in detail
    assert "courtClosed ? 'Still checked in'" in detail
    assert "? `<button type=\"button\" data-cd-suggest>" in detail


def test_primary_court_actions_and_conditions_are_not_hidden_in_an_overflow():
    detail = section(APP, "async function openCourtDetail", "function openCheckInSheet")

    assert 'class="cd-quick-actions" role="group" aria-label="Court actions"' in detail
    for control in ('id="cd-favorite"', 'id="cd-share"', 'id="cd-gallery"',
                    'id="cd-add-photo"', 'id="cd-condition"'):
        assert control in detail
    assert "Report conditions" in detail
    assert detail.index("${quickActions}") < detail.index('class="card cd-now-card"')
    assert 'aria-label="More court actions"' not in detail
    assert ".cd-quick-actions" in STYLES
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in STYLES


def test_court_facts_reviews_and_reservations_are_visible_without_disclosures():
    detail = section(APP, "async function openCourtDetail", "function openCheckInSheet")
    card = section(APP, "function courtRowHtml", "function sortCourts")

    assert "const openStatusFact = courtOpenStatusFact(court);" in detail
    assert 'class="cd-hero-facts" role="list" aria-label="Court facts"' in detail
    assert 'id="cd-favorite"' in detail and "cd-hero-save" in detail
    assert 'id="cd-review-inline"' in detail
    assert "openCourtReviews" in detail
    assert "const reservationHref = businessActionHref(court.reservation_url);" in detail
    assert "Reserve a court" in detail
    assert "<b>Busiest:</b>" in detail
    assert "courtOpenStatusFact(c)" in card
    assert "c.reservation_url ? 'Online reservations available'" in card
    assert ".cd-hero-facts" in STYLES


def test_address_and_checked_in_state_keep_the_same_directions_action():
    detail = section(APP, "async function openCourtDetail", "function openCheckInSheet")
    secondary = section(detail, "const directionsAction", "const quickActions")

    assert "const mapsUrl = courtDirectionsUrl(court);" in detail
    assert '<a id="cd-address" class="cd-address-copy" href="${mapsUrl}"' in detail
    assert 'aria-label="Directions to ${esc(court.name)} (opens Maps)"' in detail
    assert secondary.count("${directionsAction}") == 2
    assert "${checkedIn ? '' : `${directionsAction}" not in secondary
    assert "navigator.clipboard.writeText(courtAddressText)" not in detail


def test_empty_sessions_offer_a_context_preserving_action_and_detail_has_a_loading_shell():
    detail = section(APP, "async function openCourtDetail", "function openCheckInSheet")

    assert 'class="court-detail-load-state" role="status" aria-live="polite"' in detail
    assert detail.index("const modal = reuseModal || openModal") < detail.index(
        "court = await api(`/courts/${normalizedCourtId}`)",
    )
    assert 'id="cd-schedule-empty">Plan the first session</button>' in detail
    assert "modal.querySelector('#cd-schedule-empty')?.addEventListener" in detail
    assert "openChildModal(modal, () => openNewGameModal" in detail
    assert "openChildModal(modal, () => openConditionSheet" in detail
    assert "openChildModal(modal, () => openCourtGallery" in detail
    assert "openChildModal(modal, () => openCourtChat" in detail
    assert 'id="cd-find-communities"' in detail
    assert "Find communities at this court" in detail
    assert "openChildModal(modal, () => openFindClubsSheet({" in detail
    assert "courtId: court.id, courtName: court.name" in detail
    assert ".court-detail-empty.is-actionable" in STYLES
    assert ".cd-community-finder-action" in STYLES
    assert ".court-detail-load-shell" in STYLES
