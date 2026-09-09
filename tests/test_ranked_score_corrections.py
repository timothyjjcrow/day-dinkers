"""Same-match score decisions preserve consent, original sides and rating history."""
import json
from datetime import timedelta

from backend.app import db
from backend.models import BlockedUser, Game, GameSessionAttendance, User, can_direct_message, utcnow
from backend.routes.games import auto_confirm_stale_scores, _finalize_game
from tests.test_game_detail_manage import app, client, register, auth, create_game


def setup_match(client, doubles=False):
    players = [register(client, f'correction-{i}', name) for i, name in enumerate(['Alex', 'Jordan', 'Sam', 'Morgan'][:4 if doubles else 2])]
    game = create_game(client, players[0], game_type='ranked', max_players=len(players))
    for player in players[1:]:
        assert client.post(f"/api/games/{game['id']}/join", headers=auth(player)).status_code == 200
    ids = [p['user']['id'] for p in players]
    payload = {'team1': ids[:len(ids)//2], 'team2': ids[len(ids)//2:], 'score_team1': 11, 'score_team2': 7}
    return game['id'], players, payload


def post(client, game_id, action, actor, payload=None, version=None):
    if version is None:
        version = db.session.get(Game, game_id).score_version
    return client.post(f'/api/games/{game_id}/{action}', json={**(payload or {}), 'expected_score_version': version}, headers=auth(actor))


def ok(response):
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def test_late_rollback_then_same_match_correction_requires_opponent_and_rates_once(client):
    game_id, (a, b), score = setup_match(client)
    first = ok(post(client, game_id, 'complete', a, score))
    assert GameSessionAttendance.query.filter_by(game_id=game_id, attended=True).count() == 0
    row = db.session.get(Game, game_id)
    row.score_submitted_at = utcnow() - timedelta(hours=73)
    db.session.commit()
    auto_confirm_stale_scores()
    assert db.session.get(User, a['user']['id']).ranked_wins == 1
    assert GameSessionAttendance.query.filter_by(game_id=game_id, attended=True).count() == 2
    disputed = ok(post(client, game_id, 'dispute', b, {'details': 'We won that game.'}))
    assert disputed['status'] == 'unresolved' and disputed['can_propose_score_correction']
    assert [(u.rating, u.ranked_wins, u.ranked_losses) for u in User.query.order_by(User.id)] == [(1200, 0, 0), (1200, 0, 0)]
    before = disputed['score_history']
    proposed = ok(post(client, game_id, 'complete', b, {**score, 'score_team1': 7, 'score_team2': 11}))
    assert proposed['id'] == game_id and proposed['score_correction_pending']
    assert proposed['score_auto_confirms_at'] is None
    assert proposed['score_history'][:len(before)] == before
    assert post(client, game_id, 'confirm', b).status_code == 403
    row.score_submitted_at = utcnow() - timedelta(hours=73)
    db.session.commit()
    auto_confirm_stale_scores()
    assert row.status == 'awaiting_confirmation'
    confirmed = ok(post(client, game_id, 'confirm', a))
    assert confirmed['status'] == 'completed' and not confirmed['score_correction_pending']
    assert [(u.ranked_wins, u.ranked_losses) for u in User.query.order_by(User.id)] == [(0, 1), (1, 0)]
    settled = [(u.rating, u.ranked_wins, u.ranked_losses) for u in User.query.order_by(User.id)]
    assert post(client, game_id, 'confirm', a).status_code == 400
    assert _finalize_game(row, actor_id=a['user']['id']) is False
    assert [(u.rating, u.ranked_wins, u.ranked_losses) for u in User.query.order_by(User.id)] == settled
    kinds = [event['kind'] for event in confirmed['score_history']]
    assert kinds == ['reported', 'confirmed', 'late_disputed', 'rating_removed', 'correction_proposed', 'correction_confirmed']
    assert confirmed['score_history'][0]['score_team1'] == 11
    assert confirmed['score_history'][1]['confirmation_kind'] == 'timeout'
    assert confirmed['score_history'][-1]['actor_id'] == a['user']['id']


def test_two_rejected_proposals_close_without_rating_or_extending_deadline(client):
    game_id, (a, b), score = setup_match(client)
    ok(post(client, game_id, 'complete', a, score))
    initial = ok(post(client, game_id, 'dispute', b, {'details': 'Wrong score'}))
    for attempt in (1, 2):
        proposed = ok(post(client, game_id, 'complete', a, score))
        assert proposed['score_correction_attempts'] == attempt
        rejected = ok(post(client, game_id, 'dispute', b, {'details': 'Still incorrect'}))
        assert rejected['ranked_correction_deadline_at'] == initial['ranked_correction_deadline_at']
    assert not rejected['can_propose_score_correction']
    assert post(client, game_id, 'complete', a, score).get_json()['error'] == 'score_correction_closed'
    assert all(u.rating == 1200 and u.ranked_wins == 0 for u in User.query.all())


def test_expired_correction_never_confirms_and_maintenance_preserves_history(client):
    game_id, (a, b), score = setup_match(client)
    ok(post(client, game_id, 'complete', a, score))
    ok(post(client, game_id, 'dispute', b, {'details': 'Wrong score'}))
    ok(post(client, game_id, 'complete', a, score))
    row = db.session.get(Game, game_id)
    history = json.loads(row.score_history)
    history[1]['at'] = (utcnow() - timedelta(days=8)).isoformat() + 'Z'
    row.score_history = json.dumps(history)
    db.session.commit()
    assert post(client, game_id, 'confirm', b).get_json()['error'] == 'score_correction_closed'
    auto_confirm_stale_scores()
    assert row.status == 'unresolved' and not row.score_correction_pending
    assert json.loads(row.score_history)[-1]['kind'] == 'correction_expired'
    assert post(client, game_id, 'complete', a, score).status_code == 409
    assert all(u.rating == 1200 for u in User.query.all())


def test_stale_unknown_and_original_team_permissions(client):
    game_id, people, score = setup_match(client, doubles=True)
    a, teammate, b, opposing_teammate = people
    outsider = register(client, 'correction-outsider', 'Outsider')
    reported = ok(post(client, game_id, 'complete', a, score))
    assert post(client, game_id, 'confirm', teammate).status_code == 403
    assert post(client, game_id, 'confirm', outsider).status_code == 403
    assert post(client, game_id, 'dispute', outsider, {'details': 'Wrong score'}).status_code == 403
    assert post(client, game_id, 'complete', b, score, version=0).status_code == 409
    assert post(client, game_id, 'confirm', b, version=0).status_code == 409
    assert client.post(f'/api/games/{game_id}/confirm', headers=auth(b)).status_code == 409
    disputed = ok(post(client, game_id, 'dispute', b, {'details': 'Wrong score'}))
    switched = {**score, 'team1': score['team2'], 'team2': score['team1']}
    assert post(client, game_id, 'complete', b, switched).get_json()['error'] == 'original_sides_required'
    proposed = ok(post(client, game_id, 'complete', b, score))
    assert post(client, game_id, 'confirm', opposing_teammate).status_code == 403
    assert post(client, game_id, 'confirm', teammate, version=reported['score_version']).status_code == 409
    assert post(client, game_id, 'dispute', a, {'details': 'Old view'}, version=disputed['score_version']).status_code == 409
    confirmed = ok(post(client, game_id, 'confirm', teammate))
    assert confirmed['score_history'][-1]['sides'][0]['user_id'] == a['user']['id']
    outsider_view = client.get(f'/api/games/{game_id}', headers=auth(outsider)).get_json()
    assert outsider_view['score_history'] == []


def test_block_does_not_strand_existing_match_or_restore_direct_contact(client):
    game_id, (a, b), score = setup_match(client)
    ok(post(client, game_id, 'complete', a, score))
    ok(post(client, game_id, 'dispute', b, {'details': 'Wrong score'}))
    db.session.add(BlockedUser(blocker_id=b['user']['id'], blocked_id=a['user']['id']))
    db.session.commit()
    view = client.get(f'/api/games/{game_id}', headers=auth(a)).get_json()
    assert view['can_propose_score_correction']
    ok(post(client, game_id, 'complete', a, {**score, 'score_team2': 9}))
    opponent_view = client.get(f'/api/games/{game_id}', headers=auth(b)).get_json()
    assert opponent_view['awaiting_your_confirmation']
    assert not can_direct_message(a['user']['id'], b['user']['id'])
    confirmed = ok(post(client, game_id, 'confirm', b))
    assert confirmed['status'] == 'completed'
    assert not can_direct_message(a['user']['id'], b['user']['id'])
