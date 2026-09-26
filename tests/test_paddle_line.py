"""Paddle line: a first-come court rotation stored on fresh check-ins."""
from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import CheckIn, Court, Message, Notification, User, utcnow


@pytest.fixture()
def app():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        db.session.add(Court(
            name='Rotation Park', city='Irvine', state='CA',
            latitude=33.68, longitude=-117.82, num_courts=2,
        ))
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def court_id(app):
    return Court.query.filter_by(name='Rotation Park').one().id


def register(client, name):
    slug = name.lower().replace(' ', '-')
    response = client.post('/api/auth/register', json={
        'email': f'{slug}@example.com', 'password': 'secret123',
        'display_name': name,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def auth(account):
    return {'Authorization': f"Bearer {account['token']}"}


def uid(account):
    return account['user']['id']


def check_in(client, account, court_id, looking=True):
    response = client.post(
        f'/api/courts/{court_id}/checkin', json={'looking_for_game': looking},
        headers=auth(account),
    )
    assert response.status_code == 200, response.get_json()


def join(client, account, court_id):
    response = client.post(
        f'/api/courts/{court_id}/queue', json={'action': 'join'},
        headers=auth(account),
    )
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def call_next(client, account, court_id, court, expected, score=None):
    body = {'court': court, 'expected': expected}
    if score is not None:
        body['score'] = score
    return client.post(
        f'/api/courts/{court_id}/queue/next', json=body, headers=auth(account),
    )


def line_of(client, court_id, names):
    players = [register(client, name) for name in names]
    for player in players:
        check_in(client, player, court_id)
        join(client, player, court_id)
    return players


def team_names(court):
    return [[person['name'] for person in team] for team in court['teams']]


def test_joining_needs_a_fresh_check_in_here_and_refreshes_presence(client, court_id):
    ana = register(client, 'Ana Lopez')
    refused = client.post(
        f'/api/courts/{court_id}/queue', json={'action': 'join'}, headers=auth(ana),
    )
    assert refused.status_code == 409
    assert refused.get_json() == {'error': 'checkin_required'}
    assert client.get(f'/api/courts/{court_id}/queue').status_code == 401

    check_in(client, ana, court_id)
    row = CheckIn.query.filter_by(user_id=uid(ana), checked_out_at=None).one()
    row.last_presence_ping_at = utcnow() - timedelta(minutes=20)
    db.session.commit()

    queue = join(client, ana, court_id)
    assert queue == {
        'court_count': 2, 'waiting_count': 1, 'courts': [],
        'in_line': True, 'my_court': None, 'my_position': 1,
    }
    db.session.expire_all()
    row = CheckIn.query.filter_by(user_id=uid(ana), checked_out_at=None).one()
    assert row.last_presence_ping_at > utcnow() - timedelta(minutes=1)
    # Joining twice keeps the original place.
    first_stamp = row.queued_at
    assert join(client, ana, court_id)['my_position'] == 1
    db.session.expire_all()
    assert db.session.get(CheckIn, row.id).queued_at == first_stamp

    detail = client.get(f'/api/courts/{court_id}', headers=auth(ana)).get_json()
    assert detail['paddle_queue'] == queue
    assert client.get(
        f'/api/courts/{court_id}/queue', headers=auth(ana),
    ).get_json() == queue

    left = client.post(
        f'/api/courts/{court_id}/queue', json={'action': 'leave'}, headers=auth(ana),
    ).get_json()
    assert left['in_line'] is False and left['waiting_count'] == 0
    bad = client.post(
        f'/api/courts/{court_id}/queue', json={'action': 'cut'}, headers=auth(ana),
    )
    assert bad.status_code == 400


def test_call_next_four_sets_teams_and_notifies_called_players(client, court_id):
    ana, ben, cy, dee, eve = line_of(
        client, court_id, ['Ana Lopez', 'Ben Wu', 'Cy Park', 'Dee Ross', 'Eve Hart'],
    )
    queue = call_next(client, cy, court_id, 1, []).get_json()
    assert queue['waiting_count'] == 1
    assert queue['my_court'] == 1 and queue['my_position'] is None
    court = queue['courts'][0]
    assert court['court'] == 1
    # Line order p1..p4 plays p1+p4 against p2+p3.
    assert team_names(court) == [['Ana', 'Dee'], ['Ben', 'Cy']]

    eve_view = client.get(f'/api/courts/{court_id}/queue', headers=auth(eve)).get_json()
    assert eve_view['my_position'] == 1 and eve_view['my_court'] is None

    notes = Notification.query.filter_by(kind='court_up').all()
    # The player who pressed the button is not pinged.
    assert sorted(note.user_id for note in notes) == sorted(
        [uid(ana), uid(ben), uid(dee)],
    )
    assert {note.title for note in notes} == {'You’re up on Court 1'}
    assert {note.action_url for note in notes} == {f'/#court/{court_id}'}


def test_game_done_rotates_players_to_the_back_and_posts_one_score(client, court_id):
    players = line_of(
        client, court_id,
        ['Ana Lopez', 'Ben Wu', 'Cy Park', 'Dee Ross', 'Eve Hart', 'Finn Cole'],
    )
    ana, ben, cy, dee, eve, finn = players
    call_next(client, eve, court_id, 1, [])
    on_court = [uid(ana), uid(ben), uid(cy), uid(dee)]

    stale = call_next(client, eve, court_id, 1, on_court[:3])
    assert stale.status_code == 409
    body = stale.get_json()
    assert body['error'] == 'queue_changed'
    assert team_names(body['paddle_queue']['courts'][0]) == [['Ana', 'Dee'], ['Ben', 'Cy']]

    done = call_next(client, finn, court_id, 1, on_court, score=[11, 7])
    assert done.status_code == 200, done.get_json()
    queue = done.get_json()
    # Eve and Finn were waiting; Ana and Ben come straight back from the back.
    assert team_names(queue['courts'][0]) == [['Eve', 'Ben'], ['Finn', 'Ana']]
    assert queue['waiting_count'] == 2
    cy_view = client.get(f'/api/courts/{court_id}/queue', headers=auth(cy)).get_json()
    dee_view = client.get(f'/api/courts/{court_id}/queue', headers=auth(dee)).get_json()
    assert (cy_view['my_position'], dee_view['my_position']) == (1, 2)

    messages = Message.query.filter_by(court_id=court_id).all()
    assert [message.body for message in messages] == [
        'Court 1 · Ana & Dee 11–7 Ben & Cy',
    ]
    assert messages[0].sender_id == uid(finn) and messages[0].conversation_id

    # The second phone tapping the same "Game done" converges.
    again = call_next(client, cy, court_id, 1, on_court, score=[11, 7])
    assert again.status_code == 409
    assert Message.query.filter_by(court_id=court_id).count() == 1
    bad_score = call_next(client, cy, court_id, 1, [], score=[11, 11])
    assert bad_score.status_code == 400
    assert bad_score.get_json() == {'error': 'invalid_scores'}


def test_two_or_three_waiting_play_singles_and_open_courts_stay_bounded(client, court_id):
    ana, ben, cy = line_of(client, court_id, ['Ana Lopez', 'Ben Wu', 'Cy Park'])
    queue = call_next(client, ana, court_id, 2, []).get_json()
    assert queue['courts'][0]['court'] == 2
    assert team_names(queue['courts'][0]) == [['Ana'], ['Ben']]
    assert queue['waiting_count'] == 1

    assert call_next(client, ana, court_id, 3, []).status_code == 400
    assert call_next(client, ana, court_id, 13, []).status_code == 400
    assert call_next(client, ana, court_id, 1, ['x']).status_code == 400

    # Only one waiting: nobody is called onto a free court.
    lone = call_next(client, cy, court_id, 1, []).get_json()
    assert [court['court'] for court in lone['courts']] == [2]

    outsider = register(client, 'Olive Stone')
    refused = call_next(client, outsider, court_id, 1, [])
    assert refused.status_code == 409
    assert refused.get_json() == {'error': 'checkin_required'}


def test_rematch_double_tap_neither_reposts_nor_repings(client, court_id):
    ana, ben, cy, dee = line_of(
        client, court_id, ['Ana Lopez', 'Ben Wu', 'Cy Park', 'Dee Ross'],
    )
    call_next(client, ana, court_id, 1, [])
    on_court = [uid(ana), uid(ben), uid(cy), uid(dee)]
    pings = Notification.query.filter_by(kind='court_up').count()
    first = call_next(client, ana, court_id, 1, on_court, score=[11, 4])
    second = call_next(client, ben, court_id, 1, on_court, score=[11, 4])
    assert first.status_code == second.status_code == 200
    assert team_names(second.get_json()['courts'][0]) == [['Ana', 'Dee'], ['Ben', 'Cy']]
    assert Message.query.filter_by(court_id=court_id).count() == 1
    assert Notification.query.filter_by(kind='court_up').count() == pings


def test_quiet_check_ins_stay_anonymous_to_strangers_and_in_court_chat(client, court_id):
    ana, ben = line_of(client, court_id, ['Ana Lopez', 'Ben Wu'])
    quiet = register(client, 'Quinn Hale')
    check_in(client, quiet, court_id, looking=False)
    join(client, quiet, court_id)
    call_next(client, quiet, court_id, 1, [])

    own = client.get(f'/api/courts/{court_id}/queue', headers=auth(quiet)).get_json()
    assert team_names(own['courts'][0]) == [['Ana'], ['Ben']]
    call_next(client, ana, court_id, 1, [uid(ana), uid(ben)], score=[11, 9])
    stranger = client.get(f'/api/courts/{court_id}/queue', headers=auth(ana)).get_json()
    assert team_names(stranger['courts'][0]) == [['Another player'], ['Ana']]
    assert stranger['courts'][0]['teams'][0][0]['id'] is None
    assert client.get(
        f'/api/courts/{court_id}/queue', headers=auth(quiet),
    ).get_json()['my_court'] == 1
    assert Message.query.filter_by(court_id=court_id).one().body == (
        'Court 1 · Ana 11–9 Ben'
    )


def test_private_people_keep_their_place_but_not_their_name(client, court_id):
    ana, ben, cy, dee = line_of(
        client, court_id, ['Ana Lopez', 'Ben Wu', 'Cy Park', 'Dee Ross'],
    )
    viewer = register(client, 'Vic Mora')
    check_in(client, viewer, court_id)
    db.session.get(User, uid(ben)).nearby_visibility = 'hidden'
    db.session.commit()
    assert client.post(
        f"/api/users/{uid(cy)}/block", headers=auth(viewer),
    ).status_code in (200, 201)
    call_next(client, ana, court_id, 1, [])

    seen = client.get(f'/api/courts/{court_id}/queue', headers=auth(viewer)).get_json()
    teams = seen['courts'][0]['teams']
    assert teams == [
        [{'id': uid(ana), 'name': 'Ana'}, {'id': uid(dee), 'name': 'Dee'}],
        [{'id': None, 'name': 'Another player'}, {'id': None, 'name': 'Another player'}],
    ]
    # The viewer's own view (with nulls) is the expected set.
    done = call_next(client, viewer, court_id, 1, [uid(ana), uid(dee), None, None])
    assert done.status_code == 200, done.get_json()

    anonymous = client.get(f'/api/courts/{court_id}').get_json()['paddle_queue']
    assert anonymous == {
        'court_count': 2, 'waiting_count': 0,
        'courts': [{'court': 1, 'player_count': 4}],
    }
    assert 'Ana' not in str(anonymous)


def test_ending_or_reviving_a_check_in_leaves_the_line(client, court_id):
    ana, ben = line_of(client, court_id, ['Ana Lopez', 'Ben Wu'])
    assert client.post('/api/checkout', headers=auth(ana)).status_code == 200
    queue = client.get(f'/api/courts/{court_id}/queue', headers=auth(ben)).get_json()
    assert queue['waiting_count'] == 1 and queue['my_position'] == 1

    row = CheckIn.query.filter_by(user_id=uid(ben), checked_out_at=None).one()
    row.last_presence_ping_at = utcnow() - timedelta(minutes=45)
    db.session.commit()
    assert client.get(
        f'/api/courts/{court_id}/queue', headers=auth(ben),
    ).get_json()['in_line'] is False

    check_in(client, ben, court_id)
    db.session.expire_all()
    revived = db.session.get(CheckIn, row.id)
    assert revived.checked_out_at is None
    assert revived.queued_at is None and revived.queue_court is None


def test_closed_courts_refuse_joining_but_allow_leaving(client, court_id):
    (ana,) = line_of(client, court_id, ['Ana Lopez'])
    db.session.get(Court, court_id).closed = True
    db.session.commit()
    other = register(client, 'Ben Wu')
    refused = client.post(
        f'/api/courts/{court_id}/queue', json={'action': 'join'}, headers=auth(other),
    )
    assert refused.status_code == 409
    assert refused.get_json() == {'error': 'court_closed'}
    assert call_next(client, ana, court_id, 1, []).status_code == 409
    left = client.post(
        f'/api/courts/{court_id}/queue', json={'action': 'leave'}, headers=auth(ana),
    )
    assert left.status_code == 200 and left.get_json()['in_line'] is False


def test_score_line_keeps_only_the_score_when_a_name_is_hidden(client, court_id):
    ana, ben, cy, dee, eve = line_of(
        client, court_id, ['Ana Lopez', 'Ben Wu', 'Cy Park', 'Dee Ross', 'Eve Hart'],
    )
    assert client.post(
        f"/api/users/{uid(eve)}/block", headers=auth(ben),
    ).status_code in (200, 201)
    call_next(client, eve, court_id, 1, [])
    done = call_next(
        client, eve, court_id, 1, [uid(ana), None, uid(cy), uid(dee)], score=[11, 5],
    )
    assert done.status_code == 200, done.get_json()
    # Eve can't see Ben, so court chat doesn't name anyone.
    assert Message.query.filter_by(court_id=court_id).one().body == (
        'Court 1 · Game finished 11–5'
    )


def test_game_done_confirms_everyone_rotating_is_still_here(client, court_id):
    players = line_of(
        client, court_id,
        ['Ana Lopez', 'Ben Wu', 'Cy Park', 'Dee Ross', 'Eve Hart', 'Finn Cole', 'Gus Lee', 'Hal Moss'],
    )
    call_next(client, players[0], court_id, 1, [])
    earlier = utcnow() - timedelta(minutes=25)
    for row in CheckIn.query.filter_by(court_id=court_id, checked_out_at=None):
        row.last_presence_ping_at = earlier
    db.session.commit()

    done = call_next(client, players[0], court_id, 1, [uid(p) for p in players[:4]])
    assert done.status_code == 200, done.get_json()
    db.session.expire_all()
    pings = {
        row.user_id: row.last_presence_ping_at
        for row in CheckIn.query.filter_by(court_id=court_id, checked_out_at=None)
    }
    # The four who finished and the four called on are all fresh again.
    assert all(pings[uid(player)] > earlier for player in players)


def test_joining_a_game_on_a_stale_check_in_starts_over_in_line(client, court_id):
    from backend.models import Game, GamePlayer

    ana, ben = line_of(client, court_id, ['Ana Lopez', 'Ben Wu'])
    host = register(client, 'Hana Bell')
    game = Game(
        court_id=court_id, creator_id=uid(host), scheduled_at=utcnow() + timedelta(hours=2),
        max_players=4, game_type='casual', visibility='open',
    )
    db.session.add(game)
    db.session.flush()
    db.session.add(GamePlayer(game_id=game.id, user_id=uid(host)))
    row = CheckIn.query.filter_by(user_id=uid(ana), checked_out_at=None).one()
    row.last_presence_ping_at = utcnow() - timedelta(minutes=45)
    db.session.commit()

    joined = client.post(
        f'/api/games/{game.id}/join', headers=auth(ana),
        json={'expected_plan_token': db.session.get(Game, game.id).plan_review_token()},
    )
    assert joined.status_code in (200, 201), joined.get_json()
    db.session.expire_all()
    revived = db.session.get(CheckIn, row.id)
    assert revived.queued_at is None and revived.queue_court is None
    queue = client.get(f'/api/courts/{court_id}/queue', headers=auth(ben)).get_json()
    assert queue['waiting_count'] == 1 and queue['my_position'] == 1


def test_an_overfull_court_still_shows_everyone():
    from backend.routes.courts import _paddle_teams

    assert _paddle_teams(['A']) == [['A'], []]
    assert _paddle_teams(['A', 'B']) == [['A'], ['B']]
    assert _paddle_teams(['A', 'B', 'C', 'D']) == [['A', 'D'], ['B', 'C']]
    assert _paddle_teams(['A', 'B', 'C', 'D', 'E']) == [['A', 'D'], ['B', 'C', 'E']]
