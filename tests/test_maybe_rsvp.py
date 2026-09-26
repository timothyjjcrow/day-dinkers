"""Maybe RSVPs: an invitee can answer "Maybe" without taking a spot."""
from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import (
    BlockedUser, Court, Game, GameInvite, GamePlayer, Notification, utcnow,
)
from backend.routes.games import send_game_reminders
from tests.session_plan_helpers import post_reviewed_game


@pytest.fixture()
def app():
    application = create_app('testing')
    with application.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(Court(
            name='Maybe Court', city='Irvine', state='CA',
            county_slug='orange-county', latitude=33.68,
            longitude=-117.82, num_courts=4,
        ))
        db.session.commit()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, slug, name):
    response = client.post('/api/auth/register', json={
        'email': f'{slug}@example.com',
        'password': 'secret123',
        'display_name': name,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def auth(person):
    return {'Authorization': f"Bearer {person['token']}"}


def uid(person):
    return person['user']['id']


def create_game(client, host, invitees, **overrides):
    payload = {
        'court_id': Court.query.one().id,
        'scheduled_at': (utcnow() + timedelta(hours=3)).isoformat() + 'Z',
        'game_type': 'casual',
        'visibility': 'private',
        'max_players': 4,
        'invite_user_ids': [uid(person) for person in invitees],
    }
    payload.update(overrides)
    response = client.post('/api/games', json=payload, headers=auth(host))
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def maybe(client, game, person, method='post'):
    return getattr(client, method)(
        f"/api/games/{game['id']}/invites/maybe", headers=auth(person),
    )


def detail(client, game, person):
    return client.get(f"/api/games/{game['id']}", headers=auth(person))


def maybe_notes(host, game):
    return Notification.query.filter_by(
        user_id=uid(host), kind='invite_maybe', related_game_id=game['id'],
    ).all()


def test_maybe_keeps_the_invite_and_tells_the_host_once(client):
    host = register(client, 'maybe-host', 'Dana Host')
    alex = register(client, 'maybe-alex', 'Alex Maybe')
    game = create_game(client, host, [alex])

    response = maybe(client, game, alex)
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body['my_invite_status'] == 'maybe'
    assert body['is_invited'] is True and body['is_joined'] is False
    # The answer holds no spot and does not reveal who else answered.
    assert body['spots_left'] == 3
    assert body['maybe_people'] == [] and body['rsvp_counts']['maybe'] == 0
    assert GameInvite.query.filter_by(game_id=game['id'], user_id=uid(alex)).one().response == 'maybe'
    # The private game stays visible to the invitee.
    assert detail(client, game, alex).get_json()['my_invite_status'] == 'maybe'

    notes = maybe_notes(host, game)
    assert len(notes) == 1
    assert notes[0].title == 'Alex Maybe might make your play session at Maybe Court'
    assert notes[0].related_user_id == uid(alex)
    assert notes[0].unread_dedupe_key == f"game-maybe:{game['id']}"

    # Repeating the answer, or undoing and redoing it, never pings again.
    assert maybe(client, game, alex).status_code == 200
    undone = maybe(client, game, alex, 'delete')
    assert undone.status_code == 200
    assert undone.get_json()['my_invite_status'] == 'pending'
    assert GameInvite.query.filter_by(game_id=game['id'], user_id=uid(alex)).one().response is None
    for notification in maybe_notes(host, game):
        notification.read = True
        notification.unread_dedupe_key = None
    db.session.commit()
    assert maybe(client, game, alex).status_code == 200
    assert len(maybe_notes(host, game)) == 1


def test_several_maybes_collapse_into_one_unread_host_item(client):
    host = register(client, 'collapse-host', 'Host')
    alex = register(client, 'collapse-alex', 'Alex')
    ben = register(client, 'collapse-ben', 'Ben')
    game = create_game(client, host, [alex, ben])
    assert maybe(client, game, alex).status_code == 200
    assert maybe(client, game, ben).status_code == 200
    assert len(maybe_notes(host, game)) == 1


def test_only_the_host_and_joined_players_see_who_said_maybe(client):
    host = register(client, 'see-host', 'Host')
    alex = register(client, 'see-alex', 'Alex Maybe')
    joined = register(client, 'see-joined', 'Jo Joined')
    other = register(client, 'see-other', 'Olive Invitee')
    stranger = register(client, 'see-stranger', 'Stranger')
    game = create_game(client, host, [alex, joined, other], visibility='open')
    assert post_reviewed_game(
        client, f"/api/games/{game['id']}/join", headers=auth(joined),
    ).status_code == 200
    assert maybe(client, game, alex).status_code == 200

    for person in (host, joined):
        view = detail(client, game, person).get_json()
        assert view['rsvp_counts']['maybe'] == 1
        assert [row['display_name'] for row in view['maybe_people']] == ['Alex Maybe']
        assert view['maybe_people'][0]['user_id'] == uid(alex)
        assert 'email' not in view['maybe_people'][0]
    for person in (other, stranger):
        view = detail(client, game, person).get_json()
        assert view['rsvp_counts']['maybe'] == 0
        assert view['maybe_people'] == []
    assert detail(client, game, other).get_json()['my_invite_status'] == 'pending'
    assert detail(client, game, stranger).get_json()['my_invite_status'] is None

    # List rows keep the count for the host but never carry the names.
    feed = client.get('/api/games?mine=1', headers=auth(host))
    assert feed.status_code == 200, feed.get_json()
    row = next(item for item in feed.get_json()['items'] if item['id'] == game['id'])
    assert row['rsvp_counts']['maybe'] == 1
    assert 'maybe_people' not in row
    # The invitee's own list row carries the answer for the Play tag.
    mine = client.get('/api/games?mine=1', headers=auth(alex)).get_json()['items']
    assert next(item for item in mine if item['id'] == game['id'])['my_invite_status'] == 'maybe'

    # A block hides the person from the host's Maybe list and count.
    db.session.add(BlockedUser(blocker_id=uid(host), blocked_id=uid(alex)))
    db.session.commit()
    view = detail(client, game, host).get_json()
    assert view['maybe_people'] == [] and view['rsvp_counts']['maybe'] == 0


def test_joining_or_declining_after_maybe_clears_it(client):
    host = register(client, 'clear-host', 'Host')
    alex = register(client, 'clear-alex', 'Alex')
    ben = register(client, 'clear-ben', 'Ben')
    game = create_game(client, host, [alex, ben])
    assert maybe(client, game, alex).status_code == 200
    assert maybe(client, game, ben).status_code == 200
    assert detail(client, game, host).get_json()['rsvp_counts']['maybe'] == 2

    joined = post_reviewed_game(
        client, f"/api/games/{game['id']}/join", headers=auth(alex),
    )
    assert joined.status_code == 200, joined.get_json()
    assert joined.get_json()['is_joined'] is True
    declined = client.post(
        f"/api/games/{game['id']}/invites/decline", headers=auth(ben),
    )
    assert declined.status_code == 200
    view = detail(client, game, host).get_json()
    assert view['rsvp_counts']['maybe'] == 0 and view['maybe_people'] == []
    assert GameInvite.query.filter_by(game_id=game['id']).count() == 0
    # Once joined, Maybe is no longer an answer.
    assert maybe(client, game, alex).get_json()['error'] == 'already_joined'


def test_maybe_guards(client):
    host = register(client, 'guard-host', 'Host')
    alex = register(client, 'guard-alex', 'Alex')
    stranger = register(client, 'guard-stranger', 'Stranger')
    game = create_game(client, host, [alex], visibility='open')

    assert client.post(f"/api/games/{game['id']}/invites/maybe").status_code == 401
    assert client.post(
        '/api/games/999999/invites/maybe', headers=auth(alex),
    ).status_code == 404
    denied = maybe(client, game, stranger)
    assert denied.status_code == 404 and denied.get_json()['error'] == 'not_invited'
    own = maybe(client, game, host)
    assert own.status_code == 400 and own.get_json()['error'] == 'already_joined'

    row = db.session.get(Game, game['id'])
    for field, value in (('is_instant', True), ('is_challenge', True), ('status', 'cancelled')):
        original = getattr(row, field)
        setattr(row, field, value)
        db.session.commit()
        blocked = maybe(client, game, alex)
        assert blocked.status_code == 409, field
        assert blocked.get_json()['error'] == 'game_not_open'
        setattr(row, field, original)
        db.session.commit()
    assert Notification.query.filter_by(kind='invite_maybe').count() == 0

    # A player across a block cannot use the endpoint to read the roster.
    db.session.add(BlockedUser(blocker_id=uid(host), blocked_id=uid(alex)))
    db.session.commit()
    hidden = maybe(client, game, alex)
    assert hidden.status_code == 404 and hidden.get_json()['error'] == 'game_not_found'


def test_maybe_leaves_the_invite_banner_and_the_to_do_list(client):
    host = register(client, 'banner-host', 'Host')
    alex = register(client, 'banner-alex', 'Alex')
    game = create_game(client, host, [alex])

    me = client.get('/api/me', headers=auth(alex)).get_json()
    assert me['active_game'] and me['active_game']['banner_state'] == 'invited'
    invite_note = next(
        item for item in client.get('/api/notifications', headers=auth(alex)).get_json()['items']
        if item['kind'] == 'game_invite_direct' and item['related_game_id'] == game['id']
    )
    assert invite_note['needs_action'] is True

    assert maybe(client, game, alex).status_code == 200
    assert client.get('/api/me', headers=auth(alex)).get_json()['active_game'] is None
    invite_note = next(
        item for item in client.get('/api/notifications', headers=auth(alex)).get_json()['items']
        if item['id'] == invite_note['id']
    )
    assert invite_note['needs_action'] is False

    assert maybe(client, game, alex, 'delete').status_code == 200
    me = client.get('/api/me', headers=auth(alex)).get_json()
    assert me['active_game'] and me['active_game']['banner_state'] == 'invited'


def test_day_before_host_reminder_mentions_maybes_only_when_there_are_some(app, client):
    host = register(client, 'remind-host', 'Host')
    alex = register(client, 'remind-alex', 'Alex')
    ben = register(client, 'remind-ben', 'Ben')

    def tomorrow(hours):
        return (utcnow() + timedelta(hours=hours)).isoformat() + 'Z'

    with_maybes = create_game(client, host, [alex, ben], scheduled_at=tomorrow(22))
    without = create_game(client, host, [alex], scheduled_at=tomorrow(26))
    assert maybe(client, with_maybes, alex).status_code == 200
    assert maybe(client, with_maybes, ben).status_code == 200

    send_game_reminders()
    bodies = {
        note.related_game_id: note.body
        for note in Notification.query.filter_by(
            user_id=uid(host), kind='game_reminder',
        )
    }
    assert bodies[with_maybes['id']] == '1 joined · 2 maybe — nudge them or invite more.'
    assert bodies[without['id']] == '1 players are signed up.'
    assert GamePlayer.query.filter_by(user_id=uid(host)).filter(
        GamePlayer.day_reminded_at.isnot(None),
    ).count() == 2


def test_legacy_invites_gain_the_answer_column_as_pending(app, client):
    from sqlalchemy import inspect, text
    from backend.app import _upgrade_schema

    host = register(client, 'legacy-host', 'Host')
    alex = register(client, 'legacy-alex', 'Alex')
    game = create_game(client, host, [alex])
    db.session.remove()
    with db.engine.begin() as connection:
        connection.execute(text('ALTER TABLE game_invite DROP COLUMN response'))
    _upgrade_schema(app)
    _upgrade_schema(app)
    assert 'response' in {
        column['name'] for column in inspect(db.engine).get_columns('game_invite')
    }
    assert detail(client, game, alex).get_json()['my_invite_status'] == 'pending'
    assert maybe(client, game, alex).get_json()['my_invite_status'] == 'maybe'
