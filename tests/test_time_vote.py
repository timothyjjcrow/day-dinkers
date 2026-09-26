""""When works?" time votes: friends pick 2-3 proposed times, then one locks."""
from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import Court, Game, GamePlayer, Notification, utcnow
from backend.routes.games import send_game_reminders, settle_game_time_votes
from tests.session_plan_helpers import post_reviewed_game


@pytest.fixture()
def app():
    application = create_app('testing')
    with application.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(Court(
            name='Vote Court', city='Irvine', state='CA',
            county_slug='orange-county', latitude=33.68,
            longitude=-117.82, num_courts=6,
        ))
        db.session.commit()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, slug):
    response = client.post('/api/auth/register', json={
        'email': f'{slug}@example.com', 'password': 'secret123',
        'display_name': slug.title(),
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def headers(person):
    return {'Authorization': f"Bearer {person['token']}"}


def stamp(when):
    return when.replace(microsecond=0).isoformat() + 'Z'


def times(*hours):
    base = utcnow().replace(minute=0, second=0, microsecond=0)
    return [stamp(base + timedelta(hours=hour)) for hour in hours]


def vote_payload(invitees, options, **overrides):
    payload = {
        'court_id': Court.query.one().id, 'game_type': 'casual',
        'visibility': 'private', 'max_players': 4,
        'invite_user_ids': [person['user']['id'] for person in invitees],
        'time_options': options, 'recurrence_timezone': 'America/Los_Angeles',
    }
    payload.update(overrides)
    return payload


def create_vote(client, host, invitees, options=None):
    response = client.post('/api/games', json=vote_payload(
        invitees, options or times(30, 54, 78),
    ), headers=headers(host))
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def vote(client, person, game_id, option_ids):
    return client.post(f'/api/games/{game_id}/time-vote',
                       json={'option_ids': option_ids}, headers=headers(person))


def test_create_uses_earliest_option_as_placeholder(client):
    host, ana, ben = (register(client, name) for name in ('host', 'ana', 'ben'))
    options = times(78, 30, 54)
    game = create_vote(client, host, [ana, ben], options)

    assert game['scheduled_at'] == sorted(options)[0]
    vote_state = game['time_vote']
    assert [row['starts_at'] for row in vote_state['options']] == sorted(options)
    assert [row['id'] for row in vote_state['options']] == ['a', 'b', 'c']
    assert all(row['count'] == 0 for row in vote_state['options'])
    assert vote_state['can_lock'] is True and vote_state['can_vote'] is False
    assert vote_state['locks_at'] == stamp(utcnow().replace(minute=0, second=0, microsecond=0)
                                           + timedelta(hours=27))
    assert set(vote_state) == {'options', 'my_votes', 'leader_id', 'locks_at', 'can_vote', 'can_lock'}

    seen = client.get(f"/api/games/{game['id']}", headers=headers(ana)).get_json()
    assert seen['time_vote']['can_vote'] is True and seen['time_vote']['can_lock'] is False
    invite = Notification.query.filter_by(user_id=ana['user']['id'], kind='game_invite_direct').one()
    assert invite.body == 'Pick the times that work for you.'


@pytest.mark.parametrize('overrides, error', [
    ({'time_options': times(30)}, 'invalid_time_options'),
    ({'time_options': times(30, 40, 50, 60)}, 'invalid_time_options'),
    ({'time_options': times(30, 30)}, 'invalid_time_options'),
    ({'time_options': ['soon', 'later'], 'scheduled_at': times(30)[0]}, 'invalid_time_options'),
    ({'time_options': [stamp(utcnow() + timedelta(minutes=20))] + times(30)}, 'invalid_time_options'),
    ({'time_options': [stamp(utcnow() + timedelta(minutes=110))] + times(30)}, 'invalid_time_options'),
    ({'visibility': 'open'}, 'time_vote_not_available'),
    ({'recurrence': 'weekly'}, 'time_vote_not_available'),
    ({'visibility': 'friends', 'invite_user_ids': []}, 'time_vote_needs_invitees'),
])
def test_create_rejects_invalid_votes(client, overrides, error):
    host, ana = register(client, 'host'), register(client, 'ana')
    friendship = client.post('/api/friends/request', json={'user_id': ana['user']['id']},
                             headers=headers(host)).get_json()
    client.post(f"/api/friends/{friendship['friendship_id']}/respond", json={'accept': True},
                headers=headers(ana))
    response = client.post('/api/games', json=vote_payload([ana], times(30, 54), **overrides),
                           headers=headers(host))
    assert response.status_code == 400, response.get_json()
    assert response.get_json()['error'] == error
    assert Game.query.count() == 0


def test_votes_replace_count_and_follow_the_roster(client):
    host, ana, ben, stranger = (register(client, name) for name in ('host', 'ana', 'ben', 'zed'))
    game = create_vote(client, host, [ana, ben])

    first = vote(client, ana, game['id'], ['a', 'b'])
    assert first.status_code == 200, first.get_json()
    assert [row['count'] for row in first.get_json()['time_vote']['options']] == [1, 1, 0]
    assert first.get_json()['time_vote']['my_votes'] == ['a', 'b']
    assert vote(client, ben, game['id'], ['b']).status_code == 200
    replaced = vote(client, ana, game['id'], ['c']).get_json()['time_vote']
    assert [row['count'] for row in replaced['options']] == [0, 1, 1]
    assert replaced['my_votes'] == ['c']
    assert replaced['leader_id'] == 'b'  # a tie goes to the earliest time

    assert vote(client, host, game['id'], ['a']).get_json()['error'] == 'time_vote_forbidden'
    assert vote(client, stranger, game['id'], ['a']).status_code == 404
    assert vote(client, ana, game['id'], ['z']).get_json()['error'] == 'invalid_time_vote'
    assert client.post(f"/api/games/{game['id']}/time-vote", json={'option_ids': 'a'},
                       headers=headers(ana)).status_code == 400

    # Declining withdraws Ana's picks without exposing who picked what.
    assert client.post(f"/api/games/{game['id']}/invites/decline",
                       headers=headers(ana)).status_code == 200
    seen = client.get(f"/api/games/{game['id']}", headers=headers(host)).get_json()['time_vote']
    assert [row['count'] for row in seen['options']] == [0, 1, 0]
    assert all(set(row) == {'id', 'starts_at', 'count'} for row in seen['options'])


def test_joined_player_can_vote_and_voting_never_joins(client):
    host, ana = register(client, 'host'), register(client, 'ana')
    game = create_vote(client, host, [ana])
    assert vote(client, ana, game['id'], ['a']).status_code == 200
    assert GamePlayer.query.filter_by(game_id=game['id'], user_id=ana['user']['id']).count() == 0

    joined = post_reviewed_game(client, f"/api/games/{game['id']}/join", headers=headers(ana))
    assert joined.status_code == 200, joined.get_json()
    after = vote(client, ana, game['id'], ['a', 'c']).get_json()['time_vote']
    assert after['my_votes'] == ['a', 'c']


def test_host_lock_sets_time_and_asks_non_voters_to_confirm(client):
    host, ana, ben, cal = (register(client, name) for name in ('host', 'ana', 'ben', 'cal'))
    game = create_vote(client, host, [ana, ben, cal])
    for person in (ana, ben):
        assert post_reviewed_game(client, f"/api/games/{game['id']}/join",
                                  headers=headers(person)).status_code == 200
    vote(client, ana, game['id'], ['b'])
    vote(client, ben, game['id'], ['a'])

    path = f"/api/games/{game['id']}/time-vote/lock"
    assert client.post(path, json={'option_id': 'b'}, headers=headers(ana)).status_code == 403
    assert client.post(path, json={'option_id': 'z'}, headers=headers(host)).status_code == 400
    locked = client.post(path, json={'option_id': 'b'}, headers=headers(host))
    assert locked.status_code == 200, locked.get_json()
    body = locked.get_json()
    assert body['time_vote'] is None
    assert body['scheduled_at'] == game['time_vote']['options'][1]['starts_at']
    assert client.post(path, json={'option_id': 'b'}, headers=headers(host)).get_json()['error'] == 'time_vote_closed'

    rows = {row.user_id: row for row in GamePlayer.query.filter_by(game_id=game['id'])}
    assert rows[ana['user']['id']].commitment_requested_at is None
    assert rows[ben['user']['id']].commitment_requested_at is not None
    notes = {note.user_id: note for note in Notification.query.filter_by(kind='game_updated')}
    assert set(notes) == {ana['user']['id'], ben['user']['id'], cal['user']['id']}
    assert notes[ana['user']['id']].title.startswith("It's ")
    assert notes[ana['user']['id']].title.endswith(' at Vote Court')
    assert notes[ben['user']['id']].body == 'Confirm you can still make it.'
    assert notes[cal['user']['id']].body == 'Join if it works for you.'


def test_open_vote_owns_the_time(client):
    host, ana = register(client, 'host'), register(client, 'ana')
    game = create_vote(client, host, [ana])
    later = times(100)[0]
    moved = client.post(f"/api/games/{game['id']}/reschedule", json={'scheduled_at': later},
                        headers=headers(host))
    assert moved.status_code == 409 and moved.get_json()['error'] == 'time_vote_open'
    edited = client.patch(f"/api/games/{game['id']}", json={'scheduled_at': later},
                          headers=headers(host))
    assert edited.status_code == 409 and edited.get_json()['error'] == 'time_vote_open'
    opened = client.patch(f"/api/games/{game['id']}", json={'visibility': 'open'},
                          headers=headers(host))
    assert opened.status_code == 409 and opened.get_json()['error'] == 'time_vote_open'
    renamed = client.patch(f"/api/games/{game['id']}", json={'title': 'Dinks'}, headers=headers(host))
    assert renamed.status_code == 200, renamed.get_json()
    assert renamed.get_json()['time_vote'] is not None


def test_vote_stays_out_of_calendars_reminders_and_conflicts(client, app):
    host, ana = register(client, 'host'), register(client, 'ana')
    game = create_vote(client, host, [ana], times(3, 30))
    token = client.get('/api/calendar/token', headers=headers(host)).get_json()['token']
    feed = client.get(f'/api/calendar/{token}.ics').get_data(as_text=True)
    assert 'Vote Court' not in feed

    # A fixed plan at a proposed time needs no conflict review.
    fixed = client.post('/api/games', json={
        'court_id': Court.query.one().id, 'game_type': 'casual', 'visibility': 'open',
        'max_players': 4, 'scheduled_at': game['time_vote']['options'][0]['starts_at'],
    }, headers=headers(host))
    assert fixed.status_code == 201, fixed.get_json()

    row = db.session.get(Game, game['id'])
    row.scheduled_at = utcnow() + timedelta(minutes=30)
    db.session.commit()
    send_game_reminders()
    assert Notification.query.filter_by(kind='game_reminder', related_game_id=game['id']).count() == 0


def test_tick_locks_when_everyone_voted(client):
    host, ana, ben = (register(client, name) for name in ('host', 'ana', 'ben'))
    game = create_vote(client, host, [ana, ben])
    vote(client, ana, game['id'], ['b', 'c'])
    settle_game_time_votes()
    assert db.session.get(Game, game['id']).time_vote_open

    vote(client, ben, game['id'], ['c'])
    settle_game_time_votes()
    db.session.expire_all()
    row = db.session.get(Game, game['id'])
    assert not row.time_vote_open
    assert row.scheduled_at.isoformat() + 'Z' == game['time_vote']['options'][2]['starts_at']
    assert Notification.query.filter_by(kind='game_updated', user_id=host['user']['id']).count() == 1


def test_tick_locks_at_deadline_dropping_passed_times(client):
    host, ana, ben = (register(client, name) for name in ('host', 'ana', 'ben'))
    game = create_vote(client, host, [ana, ben], times(30, 54, 78))
    vote(client, ana, game['id'], ['a'])
    vote(client, ben, game['id'], ['a'])  # everyone voted, but pretend "a" passed
    start = utcnow()
    settle_game_time_votes(now=start + timedelta(hours=31))
    db.session.expire_all()
    row = db.session.get(Game, game['id'])
    # "a" already passed; "b" and "c" tie at zero, so the earliest remaining wins.
    assert row.scheduled_at.isoformat() + 'Z' == game['time_vote']['options'][1]['starts_at']


def test_tick_waits_until_three_hours_before_the_earliest_time(client):
    host, ana = register(client, 'host'), register(client, 'ana')
    game = create_vote(client, host, [ana], times(30, 54))
    base = utcnow().replace(minute=0, second=0, microsecond=0)
    settle_game_time_votes(now=base + timedelta(hours=26, minutes=59))
    assert db.session.get(Game, game['id']).time_vote_open
    settle_game_time_votes(now=base + timedelta(hours=27))
    db.session.expire_all()
    row = db.session.get(Game, game['id'])
    assert not row.time_vote_open
    assert row.scheduled_at == base + timedelta(hours=30)


def test_a_vote_on_soon_times_still_gets_an_hour_to_answer(client):
    host, ana = register(client, 'host'), register(client, 'ana')
    game = create_vote(client, host, [ana], [stamp(utcnow() + timedelta(hours=2, minutes=10))] + times(30))
    created = db.session.get(Game, game['id']).created_at
    settle_game_time_votes(now=created + timedelta(minutes=59))
    assert db.session.get(Game, game['id']).time_vote_open
    settle_game_time_votes(now=created + timedelta(hours=1))
    db.session.expire_all()
    assert not db.session.get(Game, game['id']).time_vote_open


def test_a_missed_deadline_moves_the_vote_past_passed_times(client):
    host, ana = register(client, 'host'), register(client, 'ana')
    game = create_vote(client, host, [ana])
    base = utcnow().replace(minute=0, second=0, microsecond=0)
    settle_game_time_votes(now=base + timedelta(hours=31))
    db.session.expire_all()
    row = db.session.get(Game, game['id'])
    # "a" passed with no tick: friends keep voting on "b" and "c".
    assert row.time_vote_open
    assert row.scheduled_at == base + timedelta(hours=54)
    assert [option['id'] for option in row.time_vote_state(base + timedelta(hours=31))['options']] == ['b', 'c']
    assert Notification.query.filter_by(kind='game_updated').count() == 0

    # With one time left, that time is the plan.
    settle_game_time_votes(now=base + timedelta(hours=55))
    db.session.expire_all()
    row = db.session.get(Game, game['id'])
    assert not row.time_vote_open and row.scheduled_at == base + timedelta(hours=78)
    assert Notification.query.filter_by(kind='game_updated', user_id=ana['user']['id']).count() == 1


def test_a_vote_settled_after_every_time_passed_stays_quiet(client):
    host, ana = register(client, 'host'), register(client, 'ana')
    game = create_vote(client, host, [ana], times(30, 54))
    base = utcnow().replace(minute=0, second=0, microsecond=0)
    settle_game_time_votes(now=base + timedelta(hours=55))
    db.session.expire_all()
    assert not db.session.get(Game, game['id']).time_vote_open
    assert Notification.query.filter_by(kind='game_updated').count() == 0


def test_dm_plan_and_court_cards_know_the_time_is_open(client):
    from backend.models import User
    from backend.routes.chat import _shared_direct_plan
    from backend.routes.courts import _active_counts_for

    host, ana = register(client, 'host'), register(client, 'ana')
    create_vote(client, host, [ana])
    plan = _shared_direct_plan(ana['user']['id'], host['user']['id'])
    assert plan['time_vote'] is True

    court_id = Court.query.one().id
    _, games, _ = _active_counts_for([court_id], db.session.get(User, ana['user']['id']))
    assert games.get(court_id, 0) == 0
