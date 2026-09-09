"""Structured match appointments: consent, integrity, calendar and reminders."""
from datetime import timedelta

import pytest

from backend.app import db
from backend.models import (Court, Game, GamePlayer, League, LeagueMatch, LeagueMember,
                            Notification, Tournament, TournamentEntry, TournamentMatch, User, utcnow)
from backend.routes.leagues import send_league_schedule_reminders
from tests.test_pagination_notifications import app, client, register, auth


@pytest.fixture()
def schedule(client, app):
    accounts = [register(client, f'{role}@schedule.test', role) for role in ['first', 'second', 'organizer', 'outsider']]
    with app.app_context():
        court = Court(name='League court', city='Test City', state='CA', latitude=33, longitude=-117)
        db.session.add(court)
        db.session.flush()
        now = utcnow().replace(microsecond=0)
        league = League(name='Test league', court_id=court.id, organizer_id=accounts[2]['user']['id'],
                        starts_at=now, status='active', current_round=1, round_started_at=now, round_days=7)
        db.session.add(league)
        db.session.flush()
        for account in accounts[:3]:
            db.session.add(LeagueMember(league_id=league.id, user_id=account['user']['id'], box=1))
        match = LeagueMatch(league_id=league.id, round=1, box=1,
                            player1_id=accounts[0]['user']['id'], player2_id=accounts[1]['user']['id'])
        db.session.add(match)
        db.session.commit()
        return {'accounts': accounts, 'court': court.id, 'league': league.id, 'match': match.id,
                'start': now + timedelta(days=2), 'base': f'/api/leagues/{league.id}/matches/{match.id}/schedule/'}


def post(client, s, actor, path, version, **payload):
    return client.post(s['base'] + path, json={'expected_schedule_version': version, **payload}, headers=auth(s['accounts'][actor]))


def option(s, delta=0):
    return {'starts_at': (s['start'] + timedelta(hours=delta)).isoformat() + 'Z', 'court_id': s['court'], 'duration_minutes': 60}


def propose(client, s, actor=0, version=0, delta=0):
    response = post(client, s, actor, 'proposals', version, options=[option(s, delta), option(s, delta + 2)])
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def accept(client, s, actor=1, version=1):
    response = post(client, s, actor, 'respond', version, action='accept', option_id='1')
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def test_proposals_need_opponent_consent_and_agenda_uses_only_agreed_time(client, schedule):
    s = schedule
    proposed = propose(client, s)
    assert proposed['schedule_status'] == 'waiting_reply'
    assert proposed['scheduled_at'] is None
    assert post(client, s, 0, 'respond', 1, action='accept', option_id='1').status_code == 403
    agenda = client.get('/api/leagues/agenda', headers=auth(s['accounts'][0])).get_json()['items']
    assert agenda[0]['scheduled_at'] is None
    accepted = accept(client, s)
    assert accepted['schedule_status'] == 'scheduled'
    assert accepted['schedule_options'] == []
    assert accepted['scheduled_court']['id'] == s['court']
    assert accepted['schedule_version'] == 2
    assert client.get('/api/leagues/agenda', headers=auth(s['accounts'][3])).get_json()['items'] == []


def test_decline_and_reschedule_keep_existing_confirmed_plan_until_new_acceptance(client, schedule):
    s = schedule
    propose(client, s)
    declined = post(client, s, 1, 'respond', 1, action='decline').get_json()
    assert declined['schedule_status'] == 'needs_time'
    assert declined['scheduled_at'] is None
    propose(client, s, version=2)
    original = accept(client, s, version=3)
    proposed = propose(client, s, actor=1, version=4, delta=3)
    assert proposed['scheduled_at'] == original['scheduled_at']
    declined = post(client, s, 0, 'respond', 5, action='decline').get_json()
    assert declined['scheduled_at'] == original['scheduled_at']
    propose(client, s, actor=1, version=6, delta=4)
    changed = accept(client, s, actor=0, version=7)
    assert changed['scheduled_at'] != original['scheduled_at']


def test_version_required_and_stale_choices_cannot_overwrite_accepted_time(client, schedule):
    s = schedule
    assert client.post(s['base'] + 'proposals', json={'options': [option(s)]}, headers=auth(s['accounts'][0])).status_code == 400
    propose(client, s)
    assert post(client, s, 1, 'proposals', 0, options=[option(s, 5)]).status_code == 409
    accepted = accept(client, s)
    assert post(client, s, 1, 'respond', 1, action='accept', option_id='2').status_code == 409
    assert post(client, s, 0, 'cancel', 1).status_code == 409
    assert client.get('/api/leagues/agenda', headers=auth(s['accounts'][0])).get_json()['items'][0]['scheduled_at'] == accepted['scheduled_at']


def test_outsider_cannot_read_proposals_and_organizer_cannot_accept_for_players(client, schedule):
    s = schedule
    assert post(client, s, 3, 'proposals', 0, options=[option(s)]).status_code == 403
    assert post(client, s, 2, 'proposals', 0, options=[option(s)]).status_code == 403
    propose(client, s)
    assert post(client, s, 2, 'respond', 1, action='accept', option_id='1').status_code == 403
    public = client.get(f"/api/leagues/{s['league']}", headers=auth(s['accounts'][3])).get_json()['matches'][0]
    assert public['schedule_options'] == []
    assert public['schedule_proposed_by_id'] is None
    accept(client, s)
    public = client.get(f"/api/leagues/{s['league']}", headers=auth(s['accounts'][3])).get_json()['matches'][0]
    assert public['scheduled_at'] is None
    cancelled = post(client, s, 2, 'cancel', 2)
    assert cancelled.status_code == 200
    assert cancelled.get_json()['scheduled_at'] is None


@pytest.mark.parametrize('change', ['round', 'cancelled', 'reported', 'membership', 'blocked'])
def test_closed_or_unavailable_match_rejects_old_proposal(client, app, schedule, change):
    s = schedule
    propose(client, s)
    with app.app_context():
        if change == 'round': db.session.get(League, s['league']).current_round = 2
        elif change == 'cancelled': db.session.get(League, s['league']).status = 'cancelled'
        elif change == 'reported': db.session.get(LeagueMatch, s['match']).result_state = 'awaiting_confirmation'
        elif change == 'membership': LeagueMember.query.filter_by(league_id=s['league'], user_id=s['accounts'][1]['user']['id']).delete()
        db.session.commit()
    if change == 'blocked':
        client.post(f"/api/users/{s['accounts'][0]['user']['id']}/block", headers=auth(s['accounts'][1]))
    assert post(client, s, 1, 'respond', 1, action='accept', option_id='1').status_code == 409


def test_conflict_is_rechecked_when_opponent_accepts(client, app, schedule):
    s = schedule
    propose(client, s)
    with app.app_context():
        game = Game(court_id=s['court'], creator_id=s['accounts'][1]['user']['id'],
                    scheduled_at=s['start'] + timedelta(minutes=30), duration_minutes=60, status='upcoming')
        db.session.add(game)
        db.session.flush()
        db.session.add(GamePlayer(game_id=game.id, user_id=s['accounts'][1]['user']['id']))
        db.session.commit()
    assert post(client, s, 1, 'respond', 1, action='accept', option_id='1').get_json()['error'] == 'schedule_conflict'
    assert post(client, s, 0, 'proposals', 1, options=[option(s)]).get_json()['error'] == 'schedule_conflict'
    assert post(client, s, 1, 'respond', 1, action='accept', option_id='2').status_code == 200


def test_time_validation_and_round_deadline(client, schedule):
    s = schedule
    for options in [[], [option(s)] * 4, [option(s), option(s)], [{**option(s), 'duration_minutes': -1}], [{**option(s), 'court_id': True}]]:
        assert post(client, s, 0, 'proposals', 0, options=options).status_code == 400
    assert post(client, s, 0, 'proposals', 0, options=[option(s, 200)]).get_json()['error'] == 'schedule_after_round_deadline'
    assert post(client, s, 0, 'proposals', 0, options=[option(s, -60)]).status_code == 400


@pytest.mark.parametrize('competition', ['league', 'tournament'])
def test_other_competition_appointments_prevent_double_booking(client, app, schedule, competition):
    s = schedule
    with app.app_context():
        if competition == 'league':
            db.session.add(LeagueMatch(league_id=s['league'], round=1, box=1,
                player1_id=s['accounts'][0]['user']['id'], player2_id=s['accounts'][2]['user']['id'],
                scheduled_at=s['start'] - timedelta(minutes=30), scheduled_duration_minutes=60,
                scheduled_court_id=s['court']))
        else:
            tournament = Tournament(name='Other event', court_id=s['court'],
                organizer_id=s['accounts'][2]['user']['id'], starts_at=s['start'], status='active')
            db.session.add(tournament)
            db.session.flush()
            entry = TournamentEntry(tournament_id=tournament.id, player1_id=s['accounts'][0]['user']['id'])
            db.session.add(entry)
            db.session.flush()
            db.session.add(TournamentMatch(tournament_id=tournament.id, entry1_id=entry.id,
                scheduled_at=s['start'] - timedelta(minutes=15)))
        db.session.commit()
    response = post(client, s, 0, 'proposals', 0, options=[option(s)])
    assert response.status_code == 409 and response.get_json()['error'] == 'schedule_conflict'
    assert post(client, s, 0, 'proposals', 0, options=[option(s, 1)]).status_code == 200


@pytest.mark.parametrize('state', ['awaiting_confirmation', 'disputed', 'unresolved'])
def test_agenda_keeps_result_decisions_when_scheduling_has_closed(client, app, schedule, state):
    s = schedule
    with app.app_context():
        match = db.session.get(LeagueMatch, s['match'])
        match.result_state = state
        match.reported_by_id = s['accounts'][0]['user']['id']
        match.reported_at = utcnow()
        match.score1, match.score2 = 11, 7
        db.session.commit()
    rows = client.get('/api/leagues/agenda', headers=auth(s['accounts'][1])).get_json()['items']
    assert len(rows) == 1 and rows[0]['id'] == s['match'] and rows[0]['result_state'] == state
    assert rows[0]['can_propose_schedule'] is False
    assert rows[0]['can_respond_schedule'] is False
    assert rows[0]['can_confirm_result'] is (state == 'awaiting_confirmation')


def test_calendar_contains_only_confirmed_slot_and_updates_after_reschedule_cancel(client, schedule):
    s = schedule
    token = client.get('/api/calendar/token', headers=auth(s['accounts'][0])).get_json()['token']
    calendar = lambda: client.get(f'/api/calendar/{token}.ics').get_data(as_text=True)
    uid = f"UID:thirdshot-league-match-{s['match']}@thirdshot.app"
    propose(client, s)
    assert uid not in calendar()
    accept(client, s)
    text = calendar()
    assert uid in text and 'SEQUENCE:2' in text and 'LOCATION:League court' in text
    assert 'box matches' not in text
    propose(client, s, version=2, delta=3)
    assert uid in calendar()
    accept(client, s, version=3)
    assert 'SEQUENCE:4' in calendar()
    post(client, s, 0, 'cancel', 4)
    assert uid not in calendar()


def test_reminders_send_once_per_slot_and_reset_after_agreed_change(client, app, schedule):
    s = schedule
    propose(client, s)
    accept(client, s)
    with app.app_context():
        assert send_league_schedule_reminders(s['start'] - timedelta(hours=23))['reminded'] == 2
        assert send_league_schedule_reminders(s['start'] - timedelta(hours=22))['reminded'] == 0
        assert send_league_schedule_reminders(s['start'] - timedelta(minutes=45))['reminded'] == 2
        assert send_league_schedule_reminders(s['start'] - timedelta(minutes=30))['reminded'] == 0
        assert send_league_schedule_reminders(s['start'] + timedelta(minutes=1))['reminded'] == 0
    propose(client, s, version=2, delta=5)
    accept(client, s, version=3)
    with app.app_context():
        assert send_league_schedule_reminders(s['start'] - timedelta(hours=18))['reminded'] == 2
        reminder = Notification.query.filter_by(kind='league_reminder').first()
        assert reminder.action_url.endswith(f"/match/{s['match']}")
    post(client, s, 0, 'cancel', 4)
    with app.app_context():
        assert send_league_schedule_reminders(s['start'] + timedelta(hours=4.5))['reminded'] == 0


def test_activity_tracks_only_the_current_schedule_proposal(client, app, schedule):
    s = schedule
    action_items = lambda actor: client.get('/api/notifications?filter=action&limit=1', headers=auth(s['accounts'][actor])).get_json()['items']
    propose(client, s)
    assert action_items(0) == []
    assert len(action_items(1)) == 1
    with app.app_context():
        Notification.query.filter_by(user_id=s['accounts'][1]['user']['id']).update({'read': True})
        db.session.commit()
    assert len(action_items(1)) == 1  # Reading is not responding.
    propose(client, s, version=1, delta=3)
    page = client.get('/api/notifications?filter=action&limit=1', headers=auth(s['accounts'][1])).get_json()
    assert len(page['items']) == 1 and page['has_more'] is False  # Superseded proposal is excluded.
    accept(client, s, version=2)
    assert action_items(1) == []
    propose(client, s, version=3, delta=5)
    with app.app_context():
        LeagueMember.query.filter_by(league_id=s['league'], user_id=s['accounts'][0]['user']['id']).delete()
        db.session.commit()
    assert action_items(1) == []


def test_activity_league_result_actions_match_participant_and_organizer_permissions(client, app, schedule):
    s = schedule
    with app.app_context():
        match = db.session.get(LeagueMatch, s['match'])
        match.result_state = 'awaiting_confirmation'
        match.reported_by_id = s['accounts'][0]['user']['id']
        for account in s['accounts']:
            db.session.add(Notification(user_id=account['user']['id'], kind='league_match', title='Review score',
                related_league_id=s['league'], action_url=f"/#league/{s['league']}/match/{s['match']}"))
        db.session.commit()
    pending = lambda actor: client.get('/api/notifications?filter=action', headers=auth(s['accounts'][actor])).get_json()['items']
    assert [bool(pending(actor)) for actor in range(4)] == [False, True, True, False]
    with app.app_context():
        db.session.get(LeagueMatch, s['match']).result_state = 'disputed'
        db.session.commit()
    assert [bool(pending(actor)) for actor in range(4)] == [True, True, True, False]
    with app.app_context():
        db.session.get(League, s['league']).current_round = 2
        db.session.commit()
    assert not any(pending(actor) for actor in range(4))


def test_activity_tournament_result_excludes_reporter_teammate_and_closed_events(client, app, schedule):
    s = schedule
    with app.app_context():
        tournament = Tournament(name='Doubles review', court_id=s['court'], organizer_id=s['accounts'][2]['user']['id'],
            starts_at=s['start'], status='active', event_type='doubles')
        db.session.add(tournament)
        db.session.flush()
        reporter_entry = TournamentEntry(tournament_id=tournament.id, player1_id=s['accounts'][0]['user']['id'], player2_id=s['accounts'][3]['user']['id'])
        opponent_entry = TournamentEntry(tournament_id=tournament.id, player1_id=s['accounts'][1]['user']['id'])
        db.session.add_all([reporter_entry, opponent_entry])
        db.session.flush()
        match = TournamentMatch(tournament_id=tournament.id, entry1_id=reporter_entry.id, entry2_id=opponent_entry.id,
            result_state='awaiting_confirmation', reported_by_id=s['accounts'][0]['user']['id'])
        db.session.add(match)
        db.session.flush()
        for account in s['accounts']:
            db.session.add(Notification(user_id=account['user']['id'], kind='tournament_score', title='Review score',
                related_tournament_id=tournament.id, action_url=f'/#tournament/{tournament.id}/match/{match.id}'))
        # A general event link is not an exact match decision.
        db.session.add(Notification(user_id=s['accounts'][1]['user']['id'], kind='tournament_score', title='Other update',
            related_tournament_id=tournament.id, action_url=f'/#tournament/{tournament.id}'))
        db.session.commit()
        tournament_id, match_id = tournament.id, match.id
    pending = lambda actor: client.get('/api/notifications?filter=action', headers=auth(s['accounts'][actor])).get_json()['items']
    assert [len(pending(actor)) for actor in range(4)] == [0, 1, 1, 0]
    with app.app_context():
        db.session.get(TournamentMatch, match_id).result_state = 'confirmed'
        db.session.commit()
    assert not any(pending(actor) for actor in range(4))
    with app.app_context():
        match = db.session.get(TournamentMatch, match_id)
        match.result_state = 'awaiting_confirmation'
        match.reported_by_id = s['accounts'][2]['user']['id']  # Neutral organizer: either entry may confirm.
        db.session.commit()
    assert [bool(pending(actor)) for actor in range(4)] == [True, True, True, True]
    with app.app_context():
        db.session.get(Tournament, tournament_id).status = 'completed'
        db.session.commit()
    assert not any(pending(actor) for actor in range(4))


def test_multiple_conflicting_proposals_share_one_review_and_recheck_on_accept(client, app, schedule):
    s=schedule
    with app.app_context():
        for delta in (0,2):
            game=Game(court_id=s['court'],creator_id=s['accounts'][1]['user']['id'],
                scheduled_at=s['start']+timedelta(hours=delta),duration_minutes=60,status='upcoming',
                title='Private training',visibility='private')
            db.session.add(game);db.session.flush()
            db.session.add(GamePlayer(game_id=game.id,user_id=s['accounts'][1]['user']['id']))
        db.session.commit()
    body={'options':[option(s),option(s,2)]}
    blocked=post(client,s,0,'proposals',0,**body).get_json()
    assert blocked['error']=='schedule_conflict' and blocked['proposed_slot_count']==2
    assert len(blocked['conflicts'])==2
    assert all(row['title']=='Another commitment' and row['action_url'] is None for row in blocked['conflicts'])
    accepted=post(client,s,0,'proposals',0,**body,schedule_conflict_ack=blocked['schedule_conflict_token'])
    assert accepted.status_code==200,accepted.get_json()
    response=post(client,s,1,'respond',1,action='accept',option_id='1').get_json()
    assert response['error']=='schedule_conflict'
    assert response['conflicts'][0]['title']=='Private training'
    confirmed=post(client,s,1,'respond',1,action='accept',option_id='1',schedule_conflict_ack=response['schedule_conflict_token'])
    assert confirmed.status_code==200 and confirmed.get_json()['scheduled_at']
