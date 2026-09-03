"""Anonymous hash-link previews disclose only public, useful context."""
from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import Court, Game, User, utcnow


@pytest.fixture()
def client():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        court = Court(
            name='Harbor Courts', city='Long Beach', state='CA',
            latitude=33.77, longitude=-118.19, num_courts=8,
            fee_type='free',
        )
        user = User(
            email='preview@example.com', password_hash='unused',
            display_name='Dana Lee', skill_rating=3.5,
        )
        db.session.add_all([court, user])
        db.session.flush()
        db.session.add_all([
            Game(
                court_id=court.id, creator_id=user.id,
                scheduled_at=utcnow() + timedelta(days=1),
                title='Saturday doubles', visibility='open',
            ),
            Game(
                court_id=court.id, creator_id=user.id,
                scheduled_at=utcnow() + timedelta(hours=1),
                title='Invite-only game', visibility='private',
            ),
        ])
        db.session.commit()
        ids = {
            'court': court.id,
            'user': user.id,
            'public_game': Game.query.filter_by(visibility='open').one().id,
            'private_game': Game.query.filter_by(visibility='private').one().id,
        }
        yield app.test_client(), ids
        db.session.remove()
        db.drop_all()


def test_public_share_preview_names_the_real_object(client):
    http, ids = client
    court = http.get(f"/api/share-preview?kind=court&id={ids['court']}")
    player = http.get(f"/api/share-preview?kind=player&id={ids['user']}")
    game = http.get(f"/api/share-preview?kind=game&id={ids['public_game']}")

    assert court.status_code == player.status_code == game.status_code == 200
    assert court.get_json() == {
        'title': 'Harbor Courts',
        'subtitle': 'Long Beach · 8 courts · Free',
    }
    assert player.get_json() == {
        'title': 'Dana Lee',
        'subtitle': 'Player profile on Third Shot · Self-rated 3.5',
    }
    assert game.get_json()['title'] == 'Saturday doubles'
    assert 'Harbor Courts' in game.get_json()['subtitle']
    assert court.headers['Cache-Control'] == 'public, max-age=60'


def test_private_share_preview_never_leaks_game_details(client):
    http, ids = client
    response = http.get(
        f"/api/share-preview?kind=game&id={ids['private_game']}",
    )
    payload = response.get_json()
    assert response.status_code == 200
    assert payload['title'] == 'A private play session was shared with you'
    assert 'Invite-only game' not in str(payload)
    assert 'Harbor Courts' not in str(payload)
    assert response.headers['Cache-Control'] == 'private, no-store'


@pytest.mark.parametrize('query', ('kind=court&id=nope', 'kind=unknown&id=1', 'kind=court&id=0'))
def test_share_preview_rejects_invalid_targets(client, query):
    http, _ = client
    assert http.get(f'/api/share-preview?{query}').status_code == 400
