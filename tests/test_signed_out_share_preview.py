"""Anonymous hash-link previews disclose only public, useful context."""
from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import Court, Game, GamePlayer, GameWaitlist, User, utcnow


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
    assert court.get_json()['title'] == 'Harbor Courts'
    assert court.get_json()['subtitle'] == 'Long Beach · 8 courts · Free'
    assert court.get_json()['details']['court']['num_courts'] == 8
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


def test_public_detail_has_decision_facts_without_player_identities(client):
    http, ids = client
    game = db.session.get(Game, ids['public_game'])
    game.duration_minutes = 90
    game.cost_cents = 500
    game.level_min, game.level_max = 2.0, 3.0
    game.description = 'Meet by court 2.'
    db.session.add(GamePlayer(game_id=game.id, user_id=ids['user']))
    offered = User(email='offer@example.test', password_hash='unused', display_name='Secret offer name')
    db.session.add(offered)
    db.session.flush()
    db.session.add(GameWaitlist(game_id=game.id, user_id=offered.id,
        offer_status='offered', offer_expires_at=utcnow()+timedelta(minutes=10)))
    db.session.commit()
    db.session.expire_all()
    payload = http.get(f"/api/share-preview?kind=game&id={game.id}").get_json()
    detail = payload['details']
    assert detail['cost_cents'] == 500 and detail['duration_minutes'] == 90
    assert (detail['level_min'], detail['level_max']) == (2.0, 3.0)
    assert detail['player_count'] == 1 and detail['spots_left'] == game.max_players - 2
    assert detail['description'] == 'Meet by court 2.'
    assert 'Dana Lee' not in str(payload) and 'Secret offer name' not in str(payload)
    assert 'email' not in str(payload) and 'players' not in detail and 'messages' not in detail
    court = http.get(f"/api/share-preview?kind=court&id={ids['court']}").get_json()
    assert [row['id'] for row in court['details']['sessions']] == [game.id]


@pytest.mark.parametrize('visibility,is_instant', [('friends', False), ('private', False), ('open', True)])
def test_nonpublic_sessions_never_expand_anonymous_detail(client, visibility, is_instant):
    http, ids = client
    row = db.session.get(Game, ids['public_game'])
    row.visibility, row.is_instant = visibility, is_instant
    db.session.commit()
    response = http.get(f"/api/share-preview?kind=game&id={row.id}")
    assert 'details' not in response.get_json()
    assert 'Saturday doubles' not in response.get_data(as_text=True)
    assert response.headers['Cache-Control'] == 'private, no-store'
    court = http.get(f"/api/share-preview?kind=court&id={ids['court']}").get_json()
    assert court['details']['sessions'] == []


@pytest.mark.parametrize('hidden_field', ['pending_submission', 'closed'])
def test_unreviewed_or_closed_court_has_no_anonymous_preview(client, hidden_field):
    http, ids = client
    setattr(db.session.get(Court, ids['court']), hidden_field, True)
    db.session.commit()
    assert http.get(f"/api/share-preview?kind=court&id={ids['court']}").status_code == 404
    assert http.get(f"/c/{ids['court']}").status_code == 404
    for url in (f"/api/share-preview?kind=game&id={ids['public_game']}", f"/g/{ids['public_game']}"):
        assert 'Harbor Courts' not in http.get(url).get_data(as_text=True)


@pytest.mark.parametrize('visibility', ['friends', 'private'])
@pytest.mark.parametrize('status', ['upcoming', 'completed', 'cancelled', 'unresolved'])
def test_og_and_api_keep_nonpublic_game_metadata_private(client, visibility, status):
    http, ids = client
    game = db.session.get(Game, ids['public_game'])
    game.visibility, game.status = visibility, status
    game.score_team1, game.score_team2 = 11, 7
    db.session.commit()
    for url in (f"/g/{game.id}", f"/api/share-preview?kind=game&id={game.id}"):
        response = http.get(url)
        assert response.status_code == 200
        text = response.get_data(as_text=True)
        assert 'Harbor Courts' not in text and 'Saturday doubles' not in text
        assert 'Final: 11' not in text and 'Dana Lee' not in text
    assert 'Harbor Courts' in http.get(f"/c/{ids['court']}").get_data(as_text=True)


@pytest.mark.parametrize('query', ('kind=court&id=nope', 'kind=unknown&id=1', 'kind=court&id=0'))
def test_share_preview_rejects_invalid_targets(client, query):
    http, _ = client
    assert http.get(f'/api/share-preview?{query}').status_code == 400
