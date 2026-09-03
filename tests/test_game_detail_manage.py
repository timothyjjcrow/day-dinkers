"""End-to-end contracts for the game detail/manage audit fixes."""

from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import Court, Game, Notification, User, utcnow


@pytest.fixture()
def app():
    application = create_app('testing')
    with application.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(Court(
            name='Manage Court', city='Irvine', state='CA',
            county_slug='orange-county', latitude=33.68,
            longitude=-117.82, num_courts=8,
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


def create_game(client, host, **overrides):
    payload = {
        'court_id': Court.query.one().id,
        'scheduled_at': (utcnow() + timedelta(hours=2)).isoformat() + 'Z',
        'game_type': 'casual',
        'visibility': 'open',
        'max_players': 2,
    }
    payload.update(overrides)
    response = client.post('/api/games', json=payload, headers=auth(host))
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def test_waitlist_identity_is_host_only_and_manual_promotion_is_explicit(client):
    host = register(client, 'wait-host', 'Host')
    player = register(client, 'wait-player', 'Player')
    waiting = register(client, 'wait-person', 'Waiting Player')
    game = create_game(client, host)
    assert client.post(
        f"/api/games/{game['id']}/join", headers=auth(player),
    ).status_code == 200
    queued = client.post(
        f"/api/games/{game['id']}/waitlist", headers=auth(waiting),
    )
    assert queued.status_code == 200, queued.get_json()

    player_view = client.get(
        f"/api/games/{game['id']}", headers=auth(player),
    ).get_json()
    host_view = client.get(
        f"/api/games/{game['id']}", headers=auth(host),
    ).get_json()
    assert player_view['waitlist_count'] == 1
    assert player_view['waitlist_people'] == []
    assert host_view['waitlist_people'][0]['display_name'] == 'Waiting Player'
    assert host_view['waitlist_people'][0]['position'] == 1

    paused = client.patch(
        f"/api/games/{game['id']}/waitlist/settings",
        json={'auto_fill_waitlist': False}, headers=auth(host),
    )
    assert paused.status_code == 200, paused.get_json()
    assert paused.get_json()['auto_fill_waitlist'] is False
    denied = client.patch(
        f"/api/games/{game['id']}/waitlist/settings",
        json={'auto_fill_waitlist': True}, headers=auth(player),
    )
    assert denied.status_code == 403

    removed = client.post(
        f"/api/games/{game['id']}/remove/{player['user']['id']}",
        headers=auth(host),
    )
    assert removed.status_code == 200, removed.get_json()
    assert removed.get_json()['waitlist_count'] == 1
    promoted = client.post(
        f"/api/games/{game['id']}/waitlist/{waiting['user']['id']}/promote",
        headers=auth(host),
    )
    assert promoted.status_code == 200, promoted.get_json()
    assert promoted.get_json()['promoted_user_id'] == waiting['user']['id']
    assert waiting['user']['id'] in {
        row['user_id'] for row in promoted.get_json()['players']
    }


def test_personal_invite_has_context_and_decline_notifies_host(client):
    host = register(client, 'invite-host', 'Dana Host')
    invitee = register(client, 'invite-player', 'Invitee')
    game = create_game(
        client, host, visibility='private',
        invite_user_ids=[invitee['user']['id']],
    )

    detail = client.get(
        f"/api/games/{game['id']}", headers=auth(invitee),
    )
    assert detail.status_code == 200, detail.get_json()
    assert detail.get_json()['my_invite_status'] == 'pending'
    assert detail.get_json()['is_invited'] is True
    assert detail.get_json()['invited_by']['display_name'] == 'Dana Host'

    declined = client.post(
        f"/api/games/{game['id']}/invites/decline", headers=auth(invitee),
    )
    assert declined.status_code == 200
    assert declined.get_json() == {'declined': True}
    assert Notification.query.filter_by(
        user_id=host['user']['id'], kind='invite_declined',
        related_game_id=game['id'],
    ).count() == 1
    assert client.get(
        f"/api/games/{game['id']}", headers=auth(invitee),
    ).status_code == 404


def test_score_correction_counter_deadline_and_second_dispute_are_durable(client):
    host = register(client, 'score-host', 'Score Host')
    opponent = register(client, 'score-opponent', 'Opponent')
    game = create_game(
        client, host, game_type='ranked', visibility='private',
        invite_user_ids=[opponent['user']['id']],
    )
    assert client.post(
        f"/api/games/{game['id']}/join", headers=auth(opponent),
    ).status_code == 200
    row = db.session.get(Game, game['id'])
    row.scheduled_at = utcnow() - timedelta(hours=1)
    db.session.commit()
    teams = {
        'team1': [host['user']['id']],
        'team2': [opponent['user']['id']],
    }

    reported = client.post(f"/api/games/{game['id']}/complete", json={
        **teams, 'score_team1': 11, 'score_team2': 7,
    }, headers=auth(host))
    assert reported.status_code == 200, reported.get_json()
    body = reported.get_json()
    assert body['status'] == 'awaiting_confirmation'
    assert body['can_fix_score'] is True
    assert body['score_auto_confirms_at']
    assert 71 * 60 * 60 < body['score_auto_confirm_seconds'] <= 72 * 60 * 60
    opposing_view = client.get(
        f"/api/games/{game['id']}", headers=auth(opponent),
    ).get_json()
    assert opposing_view['awaiting_your_confirmation'] is True
    assert opposing_view['can_fix_score'] is False

    correction = client.post(
        f"/api/games/{game['id']}/dispute", json={'reason': 'correction'},
        headers=auth(host),
    )
    assert correction.status_code == 200, correction.get_json()
    assert correction.get_json()['score_dispute_outcome'] == 'correction'
    assert correction.get_json()['score_correction_prefill'] == {
        'score_team1': 11, 'score_team2': 7,
        'score_games': [{
            'game_number': 1, 'score_team1': 11, 'score_team2': 7,
        }],
    }
    assert client.post(f"/api/games/{game['id']}/complete", json={
        **teams, 'score_team1': 11, 'score_team2': 8,
    }, headers=auth(host)).status_code == 200

    missing_reason = client.post(
        f"/api/games/{game['id']}/dispute", headers=auth(opponent),
    )
    assert missing_reason.status_code == 400
    assert missing_reason.get_json()['error'] == 'dispute_reason_required'
    first_dispute = client.post(
        f"/api/games/{game['id']}/dispute",
        json={'details': 'The final score was 11–8.'}, headers=auth(opponent),
    )
    assert first_dispute.status_code == 200, first_dispute.get_json()
    assert first_dispute.get_json()['score_dispute_outcome'] == 'counter_score'
    assert first_dispute.get_json()['score_dispute_count'] == 1
    counter = client.post(f"/api/games/{game['id']}/complete", json={
        **teams, 'score_team1': 8, 'score_team2': 11,
    }, headers=auth(opponent))
    assert counter.status_code == 200, counter.get_json()
    unresolved = client.post(
        f"/api/games/{game['id']}/dispute",
        json={'details': 'We remember the opposite result.'}, headers=auth(host),
    )
    assert unresolved.status_code == 200, unresolved.get_json()
    assert unresolved.get_json()['status'] == 'unresolved'
    assert unresolved.get_json()['score_dispute_outcome'] == 'unresolved'
    assert unresolved.get_json()['score_dispute_count'] == 2
    assert all(player['rating_delta'] is None for player in unresolved.get_json()['players'])
    assert db.session.get(User, host['user']['id']).rating == 1200
    assert db.session.get(User, opponent['user']['id']).rating == 1200
    history = client.get('/api/games/history', headers=auth(host)).get_json()
    assert history['unresolved_count'] == 1
    assert history['items'][0]['status'] == 'unresolved'


def test_score_reminder_timeout_provenance_and_late_dispute_rollback(client):
    from backend.routes.games import auto_confirm_stale_scores

    host = register(client, 'timeout-host', 'Timeout Host')
    opponent = register(client, 'timeout-opponent', 'Timeout Opponent')
    game = create_game(
        client, host, game_type='ranked', visibility='private',
        invite_user_ids=[opponent['user']['id']],
    )
    assert client.post(
        f"/api/games/{game['id']}/join", headers=auth(opponent),
    ).status_code == 200
    row = db.session.get(Game, game['id'])
    row.scheduled_at = utcnow() - timedelta(hours=1)
    db.session.commit()
    submitted = client.post(f"/api/games/{game['id']}/complete", json={
        'team1': [host['user']['id']],
        'team2': [opponent['user']['id']],
        'score_team1': 11,
        'score_team2': 6,
    }, headers=auth(host))
    assert submitted.status_code == 200, submitted.get_json()

    row = db.session.get(Game, game['id'])
    row.score_submitted_at = utcnow() - timedelta(hours=13)
    db.session.commit()
    auto_confirm_stale_scores()
    db.session.expire_all()
    row = db.session.get(Game, game['id'])
    assert row.status == 'awaiting_confirmation'
    assert row.score_confirmation_reminded_at is not None
    assert Notification.query.filter_by(
        user_id=opponent['user']['id'],
        kind='score_confirmation_reminder',
        related_game_id=game['id'],
    ).count() == 1
    auto_confirm_stale_scores()
    assert Notification.query.filter_by(
        user_id=opponent['user']['id'],
        kind='score_confirmation_reminder',
        related_game_id=game['id'],
    ).count() == 1

    row.score_submitted_at = utcnow() - timedelta(hours=73)
    db.session.commit()
    auto_confirm_stale_scores()
    db.session.expire_all()
    detail = client.get(
        f"/api/games/{game['id']}", headers=auth(opponent),
    ).get_json()
    assert detail['status'] == 'completed'
    assert detail['score_confirmation_kind'] == 'timeout'
    assert detail['score_confirmed_by'] == 'timeout'
    assert detail['confirmed_automatically'] is True
    assert detail['can_late_dispute'] is True
    assert detail['late_dispute_deadline_at']
    assert db.session.get(User, host['user']['id']).rating == 1216
    assert db.session.get(User, opponent['user']['id']).rating == 1184

    disputed = client.post(
        f"/api/games/{game['id']}/dispute",
        json={'details': 'The recorded score is not what we played.'},
        headers=auth(opponent),
    )
    assert disputed.status_code == 200, disputed.get_json()
    assert disputed.get_json()['score_dispute_outcome'] == 'late_dispute'
    assert disputed.get_json()['status'] == 'unresolved'
    assert disputed.get_json()['score_confirmation_kind'] == 'late_disputed'
    assert all(
        player['rating_delta'] is None
        for player in disputed.get_json()['players']
    )
    db.session.expire_all()
    assert db.session.get(User, host['user']['id']).rating == 1200
    assert db.session.get(User, opponent['user']['id']).rating == 1200


def test_casual_score_has_bounded_correction_window_and_notifies_peers(client):
    host = register(client, 'casual-score-host', 'Casual Host')
    opponent = register(client, 'casual-score-opponent', 'Casual Opponent')
    game = create_game(client, host, game_type='casual')
    assert client.post(
        f"/api/games/{game['id']}/join", headers=auth(opponent),
    ).status_code == 200
    row = db.session.get(Game, game['id'])
    row.scheduled_at = utcnow() - timedelta(hours=1)
    db.session.commit()
    teams = {
        'team1': [host['user']['id']],
        'team2': [opponent['user']['id']],
    }
    saved = client.post(f"/api/games/{game['id']}/complete", json={
        **teams, 'score_team1': 11, 'score_team2': 8,
    }, headers=auth(host))
    assert saved.status_code == 200, saved.get_json()
    assert saved.get_json()['can_fix_score'] is True
    assert saved.get_json()['score_correction_deadline_at']

    corrected = client.post(f"/api/games/{game['id']}/complete", json={
        **teams, 'score_team1': 11, 'score_team2': 9,
    }, headers=auth(host))
    assert corrected.status_code == 200, corrected.get_json()
    assert corrected.get_json()['score_correction_outcome'] == 'corrected'
    assert corrected.get_json()['score_team2'] == 9
    assert Notification.query.filter_by(
        user_id=opponent['user']['id'],
        kind='score_corrected', related_game_id=game['id'],
    ).count() == 1

    row = db.session.get(Game, game['id'])
    row.completed_at = utcnow() - timedelta(minutes=16)
    db.session.commit()
    expired = client.post(f"/api/games/{game['id']}/complete", json={
        **teams, 'score_team1': 11, 'score_team2': 10,
    }, headers=auth(host))
    assert expired.status_code == 400
    assert expired.get_json()['error'] == 'game_not_open'


def test_host_can_choose_successor_and_leave_response_explains_outcome(client):
    host = register(client, 'leave-host', 'Original Host')
    first = register(client, 'leave-first', 'First Player')
    chosen = register(client, 'leave-chosen', 'Chosen Host')
    game = create_game(client, host, max_players=4)
    for person in (first, chosen):
        assert client.post(
            f"/api/games/{game['id']}/join", headers=auth(person),
        ).status_code == 200

    left = client.post(f"/api/games/{game['id']}/leave", json={
        'transfer_to_user_id': chosen['user']['id'],
    }, headers=auth(host))
    assert left.status_code == 200, left.get_json()
    assert left.get_json()['leave_outcome'] == 'host_transferred'
    assert left.get_json()['new_host_id'] == chosen['user']['id']
    assert left.get_json()['new_host_name'] == 'Chosen Host'
    assert left.get_json()['creator_id'] == chosen['user']['id']


def test_game_chat_preview_and_unread_count_ignore_your_own_messages(client):
    host = register(client, 'chat-host', 'Chat Host')
    player = register(client, 'chat-player', 'Chat Player')
    game = create_game(client, host)
    assert client.post(
        f"/api/games/{game['id']}/join", headers=auth(player),
    ).status_code == 200
    sent = client.post(
        f"/api/games/{game['id']}/chat", json={'body': 'Running five minutes late'},
        headers=auth(host),
    )
    assert sent.status_code == 201, sent.get_json()

    host_view = client.get(
        f"/api/games/{game['id']}", headers=auth(host),
    ).get_json()
    player_view = client.get(
        f"/api/games/{game['id']}", headers=auth(player),
    ).get_json()
    assert host_view['chat_unread'] == 0
    assert player_view['chat_unread'] == 1
    assert player_view['chat_preview']['sender_name'] == 'Chat Host'
    assert player_view['chat_preview']['body'] == 'Running five minutes late'
    chat = client.get(
        f"/api/games/{game['id']}/chat", headers=auth(player),
    )
    assert chat.status_code == 200, chat.get_json()
    assert [person['display_name'] for person in chat.get_json()['game']['players']] == [
        'Chat Host', 'Chat Player',
    ]
    assert client.get(
        f"/api/games/{game['id']}", headers=auth(player),
    ).get_json()['chat_unread'] == 0
