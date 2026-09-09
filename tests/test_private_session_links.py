"""A private session link is a revocable invitation, never an automatic RSVP."""
from datetime import timedelta
import pytest
from backend.app import create_app, db
from backend.models import Court, Game, GameInvite, GamePlayer, User, utcnow


@pytest.fixture()
def setup():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        client = app.test_client()
        accounts = []
        for i in range(3):
            response = client.post('/api/auth/register', json={
                'email': f'link-{i}@example.test', 'display_name': f'Player {i}',
                'password': 'test-password',
            }).get_json()
            accounts.append((response['user']['id'], {'Authorization': f"Bearer {response['token']}"}))
        court = Court(name='Link Court', latitude=45.5, longitude=-122.7, city='Portland')
        db.session.add(court); db.session.commit()
        yield client, accounts, court
        db.session.remove(); db.drop_all()


def create_private(setup, **extra):
    client, accounts, court = setup
    payload = {'court_id': court.id, 'scheduled_at': (utcnow()+timedelta(days=2)).isoformat(),
               'max_players': 4, 'game_type': 'casual', 'visibility': 'private',
               'title': 'Wednesday doubles', 'cost_cents': 0, 'duration_minutes': 90,
               'invite_link_enabled': True, **extra}
    response = client.post('/api/games', headers=accounts[0][1], json=payload)
    assert response.status_code == 201, response.get_json()
    game = response.get_json()
    link = client.get(f"/api/games/{game['id']}/invite-link", headers=accounts[0][1]).get_json()
    return game, link, link['url'].rsplit('/', 1)[1], payload


def test_new_host_can_make_private_plan_and_preview_does_not_grant_access(setup):
    client, accounts, _ = setup
    game, link, token, _ = create_private(setup)
    path = f"/api/games/{game['id']}"
    assert link['enabled'] is True and link['version'] == 1
    assert client.get(path, headers=accounts[1][1]).status_code == 404
    response = client.post(path+'/invite-link/preview', json={'token': token})
    preview = response.get_json()
    assert response.status_code == 200 and 'no-store' in response.headers['Cache-Control']
    assert preview['title'] == 'Wednesday doubles' and preview['host_name'] == 'Player 0'
    assert preview['court']['name'] == 'Link Court' and preview['joined_count'] == 1
    assert not {'players', 'invites', 'chat_preview', 'waitlist_people', 'creator_id'} & set(preview)
    assert GameInvite.query.count() == 0 and GamePlayer.query.count() == 1
    assert token not in str(game)
    response = client.get('/api/games?lat=45.5&lng=-122.7&radius=25', headers=accounts[1][1])
    assert response.status_code == 200
    assert response.get_json()['items'] == []


def test_opening_invitation_then_joining_are_distinct_and_idempotent(setup):
    client, accounts, _ = setup
    game, link, token, _ = create_private(setup)
    path = f"/api/games/{game['id']}"
    for _ in range(2):
        response = client.post(path+'/invite-link/redeem', headers=accounts[1][1], json={'token': token})
        assert response.status_code == 200
        assert response.get_json()['is_invited'] and not response.get_json()['is_joined']
    assert GameInvite.query.count() == 1 and GamePlayer.query.count() == 1
    detail = client.get(path, headers=accounts[1][1]).get_json()
    assert len(detail['players']) == 1
    response = client.post(path+'/join', headers=accounts[1][1], json={})
    assert response.status_code == 200 and response.get_json()['is_joined']
    assert GamePlayer.query.count() == 2
    # Revocation closes the link; a previously accepted invitation/RSVP stays.
    assert client.delete(path+'/invite-link', headers=accounts[0][1], json={'expected_version': 1}).status_code == 200
    assert client.get(path, headers=accounts[1][1]).status_code == 200
    assert client.post(path+'/invite-link/redeem', headers=accounts[2][1], json={'token': token}).status_code == 404


def test_only_current_host_can_manage_and_rotation_rejects_old_or_stale_links(setup):
    client, accounts, _ = setup
    game, link, token, _ = create_private(setup)
    path = f"/api/games/{game['id']}"
    assert client.get(path+'/invite-link', headers=accounts[1][1]).status_code == 404
    assert client.post(path+'/invite-link', headers=accounts[0][1], json={}).status_code == 428
    rotated = client.post(path+'/invite-link', headers=accounts[0][1], json={'expected_version': 1}).get_json()
    assert rotated['version'] == 2 and rotated['url'] != link['url']
    assert client.post(path+'/invite-link/preview', json={'token': token}).status_code == 404
    assert client.delete(path+'/invite-link', headers=accounts[0][1], json={'expected_version': 1}).status_code == 409
    assert client.post(path+'/invite-link/preview', json={'token': rotated['url'].rsplit('/', 1)[1]}).status_code == 200


@pytest.mark.parametrize('change', ['expired', 'cancelled', 'closed', 'ownership'])
def test_link_cannot_outlive_its_date_state_or_owner(setup, change):
    client, accounts, court = setup
    payload, _, token, _ = create_private(setup)
    game = db.session.get(Game, payload['id'])
    if change == 'expired': game.invite_link_expires_at = utcnow()-timedelta(seconds=1)
    elif change == 'cancelled': game.status = 'cancelled'
    elif change == 'closed': court.closed = True
    else: game.creator_id = accounts[2][0]
    db.session.commit()
    path = f'/api/games/{game.id}/invite-link'
    assert client.post(path+'/preview', json={'token': token}).status_code == 404
    assert client.post(path+'/redeem', headers=accounts[1][1], json={'token': token}).status_code == 404
    assert GameInvite.query.count() == 0


def test_wrong_game_token_and_blocked_recipient_do_not_gain_private_access(setup):
    from backend.models import BlockedUser
    client, accounts, _ = setup
    first, _, token, _ = create_private(setup)
    second, _, _, _ = create_private(setup, scheduled_at=(utcnow()+timedelta(days=3)).isoformat())
    assert client.post(f"/api/games/{second['id']}/invite-link/preview", json={'token': token}).status_code == 404
    db.session.add(BlockedUser(blocker_id=accounts[0][0], blocked_id=accounts[1][0])); db.session.commit()
    path = f"/api/games/{first['id']}/invite-link"
    assert client.post(path+'/preview', headers=accounts[1][1], json={'token': token}).status_code == 404
    assert client.post(path+'/redeem', headers=accounts[1][1], json={'token': token}).status_code == 404
    assert GameInvite.query.count() == 0


def test_retry_preserves_link_and_host_roster(setup):
    client, accounts, _ = setup
    game, link, _, payload = create_private(setup, client_attempt_id='private-link-attempt-001')
    response = client.post('/api/games', headers=accounts[0][1], json=payload)
    assert response.status_code in (200, 201) and response.get_json()['id'] == game['id']
    assert Game.query.count() == 1 and GamePlayer.query.count() == 1
    assert client.get(f"/api/games/{game['id']}/invite-link", headers=accounts[0][1]).get_json() == link
