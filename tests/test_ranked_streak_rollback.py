"""Late score disputes rebuild current streaks from settled ranked results."""
from datetime import timedelta

import pytest

from backend.app import db
from backend.models import (
    Court, Game, Tournament, TournamentEntry, TournamentMatch, User, utcnow,
)
from backend.routes.games import auto_confirm_stale_scores
from tests.test_game_detail_manage import app, client, register, auth, create_game


def finish(client, host, opponent, *, won=True, timeout=False, ranked=True):
    game = create_game(client, host, game_type='ranked' if ranked else 'casual')
    assert client.post(f"/api/games/{game['id']}/join", headers=auth(opponent)).status_code == 200
    response = client.post(f"/api/games/{game['id']}/complete", json={
        'team1': [host['user']['id']], 'team2': [opponent['user']['id']],
        'score_team1': 11 if won else 7, 'score_team2': 7 if won else 11,
    }, headers=auth(host))
    assert response.status_code == 200, response.get_json()
    if timeout:
        row = db.session.get(Game, game['id'])
        row.score_submitted_at = utcnow() - timedelta(hours=73)
        db.session.commit()
        auto_confirm_stale_scores()
    elif ranked:
        response = client.post(f"/api/games/{game['id']}/confirm", headers=auth(opponent))
        assert response.status_code == 200, response.get_json()
    db.session.expire_all()
    return db.session.get(Game, game['id'])


def dispute(client, game, opponent):
    response = client.post(f'/api/games/{game.id}/dispute', json={
        'details': 'The automatically confirmed score is incorrect.',
    }, headers=auth(opponent))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['score_dispute_outcome'] == 'late_dispute'
    db.session.expire_all()


def test_late_dispute_restores_wins_before_removed_loss_ignoring_casual(client):
    a, b = register(client, 'restore-a', 'Ana'), register(client, 'restore-b', 'Ben')
    finish(client, a, b)
    finish(client, a, b)
    loss = finish(client, a, b, won=False, timeout=True)
    finish(client, a, b, won=False, ranked=False)
    assert db.session.get(User, a['user']['id']).current_streak == 0
    dispute(client, loss, b)
    user = db.session.get(User, a['user']['id'])
    assert (user.current_streak, user.best_streak) == (2, 2)
    assert (user.ranked_wins, user.ranked_losses) == (2, 0)


def test_late_dispute_of_old_win_does_not_shorten_streak_after_later_loss(client):
    a, b = register(client, 'old-a', 'Ana'), register(client, 'old-b', 'Ben')
    old = finish(client, a, b, timeout=True)
    finish(client, a, b, won=False)
    finish(client, a, b)
    assert db.session.get(User, a['user']['id']).current_streak == 1
    dispute(client, old, b)
    user = db.session.get(User, a['user']['id'])
    assert (user.current_streak, user.best_streak) == (1, 1)
    assert (user.ranked_wins, user.ranked_losses) == (1, 1)


@pytest.mark.parametrize('ranked,as_partner', [(True, False), (True, True), (False, False)])
def test_late_dispute_preserves_settled_tournament_streak(client, ranked, as_partner):
    from backend.routes.tournaments import _complete_tournament

    a, b = register(client, 'cup-a', 'Ana'), register(client, 'cup-b', 'Ben')
    c, d = register(client, 'cup-c', 'Cam'), register(client, 'cup-d', 'Dee')
    # Use the tournament's normal settlement path. Confirmed match order is
    # bracket order at completion, not the individual reporting timestamps.
    tournament = Tournament(
        name='Settled cup', organizer_id=a['user']['id'],
        court_id=Court.query.one().id, starts_at=utcnow(),
        status='active', ranked=ranked, format='single_elim',
        event_type='doubles' if as_partner else 'singles', max_entries=2,
    )
    db.session.add(tournament)
    db.session.flush()
    first = TournamentEntry(
        tournament_id=tournament.id,
        player1_id=c['user']['id'] if as_partner else a['user']['id'],
        player2_id=a['user']['id'] if as_partner else None,
    )
    second = TournamentEntry(
        tournament_id=tournament.id, player1_id=b['user']['id'],
        player2_id=d['user']['id'] if as_partner else None,
    )
    db.session.add_all([first, second])
    db.session.flush()
    db.session.add(TournamentMatch(
        tournament_id=tournament.id, round=1, position=0,
        entry1_id=first.id, entry2_id=second.id, winner_entry_id=first.id,
        score1=11, score2=7, result_state='confirmed', confirmed_at=utcnow(),
    ))
    db.session.flush()
    _complete_tournament(tournament, first.id)
    db.session.commit()
    loss = finish(client, a, b, won=False, timeout=True)
    dispute(client, loss, b)
    user = db.session.get(User, a['user']['id'])
    assert user.current_streak == (1 if ranked else 0)
    assert user.best_streak == (1 if ranked else 0)


def test_removing_middle_loss_joins_winning_runs_and_raises_historical_best(client):
    a, b = register(client, 'bridge-a', 'Ana'), register(client, 'bridge-b', 'Ben')
    finish(client, a, b)
    finish(client, a, b)
    loss = finish(client, a, b, won=False, timeout=True)
    finish(client, a, b)
    finish(client, a, b)
    user = db.session.get(User, a['user']['id'])
    assert (user.current_streak, user.best_streak) == (2, 2)
    dispute(client, loss, b)
    user = db.session.get(User, a['user']['id'])
    assert (user.current_streak, user.best_streak) == (4, 4)
    assert (user.ranked_wins, user.ranked_losses) == (4, 0)
    profile = client.get(f"/api/users/{a['user']['id']}", headers=auth(b)).get_json()
    assert 'hot_streak' in {badge['id'] for badge in profile['badges']}


def test_tournament_streak_uses_settlement_round_order_not_confirmation_order(client):
    from backend.routes.tournaments import _complete_tournament

    a, b = register(client, 'order-a', 'Ana'), register(client, 'order-b', 'Ben')
    c, d = register(client, 'order-c', 'Cam'), register(client, 'order-d', 'Dee')
    tournament = Tournament(
        name='Settled rounds', organizer_id=a['user']['id'],
        court_id=Court.query.one().id, starts_at=utcnow(),
        status='active', ranked=True, format='single_elim',
        event_type='singles', max_entries=4,
    )
    db.session.add(tournament)
    db.session.flush()
    entries = [TournamentEntry(tournament_id=tournament.id, player1_id=p['user']['id'])
               for p in (a, b, c, d)]
    db.session.add_all(entries)
    db.session.flush()
    now = utcnow()
    for round_, position, first, second, winner, confirmed in [
        (1, 0, 0, 2, 0, now),
        (1, 1, 1, 3, 1, now),
        # Ana's final loss was confirmed earlier than her semifinal win.
        (2, 0, 0, 1, 1, now - timedelta(minutes=5)),
        (2, 1, 2, 3, 2, now),
    ]:
        db.session.add(TournamentMatch(
            tournament_id=tournament.id, round=round_, position=position,
            entry1_id=entries[first].id, entry2_id=entries[second].id,
            winner_entry_id=entries[winner].id,
            score1=11 if winner == first else 7,
            score2=7 if winner == first else 11,
            result_state='confirmed', confirmed_at=confirmed,
        ))
    db.session.flush()
    _complete_tournament(tournament, entries[1].id)
    db.session.commit()
    user = db.session.get(User, a['user']['id'])
    assert (user.current_streak, user.best_streak) == (0, 1)
    loss = finish(client, a, b, won=False, timeout=True)
    dispute(client, loss, b)
    user = db.session.get(User, a['user']['id'])
    assert (user.current_streak, user.best_streak) == (0, 1)
