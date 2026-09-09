"""Personal plans include pending decisions and competition dates without discovery limits."""
from datetime import timedelta

import pytest
from backend.app import create_app, db
from backend.models import (
    Court, Game, GamePlayer, GameInvite, GameWaitlist, User,
    Tournament, TournamentEntry, TournamentMatch, League, LeagueMember,
    LeagueMatch, utcnow,
)


@pytest.fixture()
def setup():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        client = app.test_client()
        account = client.post('/api/auth/register', json={
            'email': 'agenda@example.test', 'password': 'test-password', 'display_name': 'Player',
        }).get_json()
        user = db.session.get(User, account['user']['id'])
        other = User(email='host@example.test', display_name='Host', password_hash='not-used')
        court = Court(name='Agenda Court', city='Portland', state='OR', latitude=45.5, longitude=-122.7)
        db.session.add_all([other, court])
        db.session.commit()
        yield client, {'Authorization': f"Bearer {account['token']}"}, user, other, court
        db.session.remove()
        db.drop_all()


def game_at(host, court, day, visibility='open'):
    game = Game(creator_id=host.id, court_id=court.id, scheduled_at=utcnow()+timedelta(days=day),
                duration_minutes=90, visibility=visibility, game_type='casual', max_players=6)
    db.session.add(game)
    db.session.flush()
    db.session.add(GamePlayer(game_id=game.id, user_id=host.id))
    return game


def test_pending_invites_and_waitlists_are_plans_without_claiming_attendance(setup):
    client, auth, user, host, court = setup
    joined = game_at(user, court, 1)
    invited = game_at(host, court, 8, 'private')
    queued = game_at(host, court, 20)
    hidden = game_at(host, court, 2, 'private')
    revoked = game_at(host, court, 4, 'private')
    db.session.add_all([
        GameInvite(game_id=invited.id, user_id=user.id),
        GameWaitlist(game_id=queued.id, user_id=user.id),
        GameWaitlist(game_id=revoked.id, user_id=user.id),
    ])
    db.session.commit()
    response = client.get('/api/play/home', headers=auth).get_json()
    items = {item['id']: item for item in response['mine']['items']}
    assert set(items) == {joined.id, invited.id, queued.id}
    assert items[invited.id]['is_invited'] and not items[invited.id]['is_joined']
    assert items[queued.id]['waitlist_position'] == 1 and not items[queued.id]['is_joined']
    assert hidden.id not in items and revoked.id not in items


def test_personal_pagination_reaches_later_dates_and_keeps_stable_order(setup):
    client, auth, user, host, court = setup
    expected = [game_at(user, court, day).id for day in (1, 8, 20)]
    db.session.commit()
    cursor, actual = None, []
    while True:
        query = {'mine': 1, 'limit': 1}
        if cursor:
            query['cursor'] = cursor
        page = client.get('/api/games', query_string=query, headers=auth).get_json()
        actual.extend(row['id'] for row in page['items'])
        if not page['has_more']:
            break
        cursor = page['next_cursor']
    assert actual == expected


def test_competition_agenda_contains_future_tournaments_specific_matches_and_leagues(setup):
    client, auth, user, host, court = setup
    tournament = Tournament(name='Later Open', court_id=court.id, organizer_id=host.id,
                            starts_at=utcnow()+timedelta(days=20), status='active', event_type='singles')
    league = League(name='Autumn Singles', court_id=court.id, organizer_id=host.id,
                    starts_at=utcnow()+timedelta(days=1), status='active', current_round=1)
    db.session.add_all([tournament, league])
    db.session.flush()
    mine = TournamentEntry(tournament_id=tournament.id, player1_id=user.id)
    opponent = TournamentEntry(tournament_id=tournament.id, player1_id=host.id)
    db.session.add_all([mine, opponent, LeagueMember(league_id=league.id, user_id=user.id),
                        LeagueMember(league_id=league.id, user_id=host.id)])
    db.session.flush()
    match = TournamentMatch(tournament_id=tournament.id, entry1_id=mine.id, entry2_id=opponent.id,
                            scheduled_at=tournament.starts_at+timedelta(hours=1), court_number=2)
    scheduled = LeagueMatch(league_id=league.id, round=1, box=1, player1_id=user.id, player2_id=host.id,
                            scheduled_at=utcnow()+timedelta(days=8), scheduled_court_id=court.id)
    arrange = LeagueMatch(league_id=league.id, round=1, box=2, player1_id=user.id, player2_id=host.id)
    db.session.add_all([match, scheduled, arrange])
    db.session.commit()
    result = client.get('/api/play/home', headers=auth)
    assert result.status_code == 200, result.get_json()
    rows = result.get_json()['competitions']
    by_key = {(row['kind'], row['id']): row for row in rows}
    assert ('tournament', tournament.id) in by_key
    assert by_key['tournament_match', match.id]['opponent_name'] == 'Host'
    assert by_key['tournament_match', match.id]['court_number'] == 2
    assert by_key['league_match', scheduled.id]['scheduled_court']['id'] == court.id
    assert by_key['league_match', arrange.id]['schedule_status'] == 'needs_time'
    assert len(rows) == 4
    user.calendar_token = 'synthetic-agenda-calendar-token'
    db.session.commit()
    calendar = client.get('/api/calendar/synthetic-agenda-calendar-token.ics').get_data(as_text=True)
    assert f'UID:thirdshot-tournament-match-{match.id}@thirdshot.app' in calendar
    assert f'/#tournament/{tournament.id}/match/{match.id}' in calendar
    assert 'STATUS:TENTATIVE' in calendar
    assert f'UID:thirdshot-league-match-{scheduled.id}@thirdshot.app' in calendar
    assert f'UID:thirdshot-league-match-{arrange.id}@thirdshot.app' not in calendar
