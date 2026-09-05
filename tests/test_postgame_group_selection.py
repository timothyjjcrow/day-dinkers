"""A post-game group invites only the reviewed co-players, within capacity."""
from backend.app import db
from backend.models import Crew, CrewInvite, CrewMember, Game, Notification, User, utcnow
from tests.test_crews import app, client, register, auth_headers, court_id, completed_game


def players(client, count):
    return [register(client, f'group-review-{i}@example.com', f'Player {i}')
            for i in range(count)]


def test_selected_coplayers_get_group_invitations_without_a_new_game_or_membership(client):
    owner, selected, omitted = players(client, 3)
    game = completed_game(client, owner, court_id(client), [owner], [selected, omitted])
    route = f"/api/games/{game['id']}/crew"
    response = client.post(route, headers=auth_headers(owner), json={
        'name': 'Morning regulars', 'invite_user_ids': [selected['user']['id']],
    })
    assert response.status_code == 201, response.get_json()
    created = response.get_json()
    assert created['invited_user_ids'] == [selected['user']['id']]
    assert created['crew']['pending_count'] == 1
    assert created['crew']['member_count'] == 1
    assert CrewMember.query.count() == 0
    assert Game.query.count() == 1
    assert {row.invitee_id for row in CrewInvite.query.all()} == {selected['user']['id']}
    assert {row.user_id for row in Notification.query.filter_by(kind='crew_invite')} == {selected['user']['id']}

    # Reopening or retrying the same source returns its durable group. It does
    # not send a second batch or silently add newly submitted invitees.
    replay = client.post(route, headers=auth_headers(owner), json={
        'invite_user_ids': [selected['user']['id'], omitted['user']['id']],
    })
    assert replay.status_code == 200
    assert replay.get_json()['created'] is False
    assert Crew.query.count() == CrewInvite.query.count() == 1


def test_source_selection_rejects_self_outsiders_no_shows_and_empty_review(client):
    owner, teammate, absent, outsider = players(client, 4)
    game = completed_game(client, owner, court_id(client), [owner], [teammate], no_shows=[absent])
    route = f"/api/games/{game['id']}/crew"
    for invalid in ([], None, [owner['user']['id']], [absent['user']['id']], [outsider['user']['id']]):
        response = client.post(route, headers=auth_headers(owner), json={'invite_user_ids': invalid})
        assert response.status_code == 400
        assert response.get_json()['error'] == 'invalid_invite_user_ids'
    assert Crew.query.count() == CrewInvite.query.count() == 0


def test_changed_selected_player_requires_another_review_before_any_invitations(client, monkeypatch):
    from backend.routes import crews as routes
    owner, first, deleted = players(client, 3)
    game = completed_game(client, owner, court_id(client), [owner], [first, deleted])
    lock = routes._lock_users_for_update

    def deletion_wins(user_ids):
        users = lock(user_ids)
        next(user for user in users if user.id == deleted['user']['id']).deleted_at = utcnow()
        return users

    monkeypatch.setattr(routes, '_lock_users_for_update', deletion_wins)
    response = client.post(f"/api/games/{game['id']}/crew", headers=auth_headers(owner), json={
        'invite_user_ids': [first['user']['id'], deleted['user']['id']],
    })
    assert response.status_code == 409
    assert response.get_json()['error'] == 'crew_invitees_changed'
    assert Crew.query.count() == CrewInvite.query.count() == 0
    assert Notification.query.filter_by(kind='crew_invite').count() == 0


def test_large_source_game_requires_selection_and_accepts_at_most_eleven_invitees(client):
    roster = players(client, 13)
    owner = roster[0]
    game = completed_game(client, owner, court_id(client), roster[:6], roster[6:])
    route = f"/api/games/{game['id']}/crew"
    for payload in ({}, {'invite_user_ids': [p['user']['id'] for p in roster[1:]]}):
        response = client.post(route, headers=auth_headers(owner), json=payload)
        assert response.status_code == 400
        assert response.get_json()['error'] == 'too_many_invitees'
        assert Crew.query.count() == 0
    chosen = [p['user']['id'] for p in roster[1:12]]
    response = client.post(route, headers=auth_headers(owner), json={'invite_user_ids': chosen})
    assert response.status_code == 201, response.get_json()
    assert response.get_json()['invited_user_ids'] == chosen
    assert CrewInvite.query.count() == 11
    assert response.get_json()['crew']['member_count'] == 1
