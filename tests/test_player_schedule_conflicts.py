"""One conflict policy for personal commitments, without leaking other plans."""
from datetime import timedelta

import pytest

from backend.app import create_app, db
from backend.models import (
    Court, Game, GamePlayer, League, LeagueMatch, LeagueMember,
    Tournament, TournamentEntry, TournamentMatch, User, utcnow,
)
from backend.services.player_schedule import player_schedule_conflicts, schedule_review_needed


@pytest.fixture()
def setup():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        players = [User(email=f'conflict-{i}@example.test', display_name=f'Player {i}', password_hash='unused') for i in range(3)]
        court = Court(name='Shared court', latitude=45, longitude=-122)
        db.session.add_all([*players, court]); db.session.flush()
        start = utcnow().replace(second=0,microsecond=0) + timedelta(days=2)
        yield app, players, court, start
        db.session.remove(); db.drop_all()


def game_for(player, court, start, **extra):
    game = Game(creator_id=player.id,court_id=court.id,scheduled_at=start,
                duration_minutes=90,title='Private existing plan',visibility='private',**extra)
    db.session.add(game); db.session.flush()
    db.session.add(GamePlayer(game=game,user_id=player.id)); db.session.commit()
    return game


def test_overlap_is_half_open_and_excludes_unjoined_or_cancelled_plans(setup):
    _, players, court, start = setup
    game = game_for(players[0],court,start)
    assert len(player_schedule_conflicts([players[0].id],start+timedelta(minutes=89),60,viewer_id=players[0].id)) == 1
    assert player_schedule_conflicts([players[0].id],start+timedelta(minutes=90),60) == []
    assert player_schedule_conflicts([players[1].id],start,60) == []
    assert player_schedule_conflicts([players[0].id],start,60,exclude_game_id=game.id) == []
    game.status='cancelled';db.session.commit()
    assert player_schedule_conflicts([players[0].id],start,60) == []


def test_acknowledgement_belongs_to_current_conflicts_and_protects_other_players_details(setup):
    _, players, court, start = setup
    game = game_for(players[0],court,start)
    args=([players[0].id],start+timedelta(minutes=15),60)
    warning = schedule_review_needed(*args,{},scope='join:9',viewer_id=players[0].id)
    row=warning['conflicts'][0]
    assert row['title']=='Private existing plan' and row['action_url']==f'/#game/{game.id}'
    payload={'schedule_conflict_ack':warning['schedule_conflict_token']}
    assert schedule_review_needed(*args,payload,scope='join:9',viewer_id=players[0].id) is None
    assert schedule_review_needed(*args,payload,scope='join:10',viewer_id=players[0].id)
    game.scheduled_at+=timedelta(minutes=5);db.session.commit()
    assert schedule_review_needed(*args,payload,scope='join:9',viewer_id=players[0].id)
    other=player_schedule_conflicts(*args,viewer_id=players[1].id)[0]
    assert other['kind']=='busy' and other['title']=='Another commitment'
    assert other['starts_at'] is None and other['ends_at'] is None and other['action_url'] is None


def test_accepted_competition_appointments_share_the_policy_but_proposals_and_voids_do_not(setup):
    _, players, court, start = setup
    league=League(name='Evening league',court_id=court.id,organizer_id=players[0].id,
                  starts_at=start,status='active',current_round=1)
    tournament=Tournament(name='Weekend event',court_id=court.id,organizer_id=players[0].id,
                          starts_at=start,status='active')
    db.session.add_all([league,tournament]);db.session.flush()
    for player in players[:2]:
        db.session.add(LeagueMember(league=league,user_id=player.id))
    league_match=LeagueMatch(league=league,round=1,box=1,player1_id=players[0].id,
        player2_id=players[1].id,scheduled_at=start,scheduled_duration_minutes=60)
    entries=[TournamentEntry(tournament=tournament,player1_id=p.id) for p in players[:2]]
    db.session.add_all([league_match,*entries]);db.session.flush()
    bracket=TournamentMatch(tournament=tournament,round=1,position=1,
        entry1_id=entries[0].id,entry2_id=entries[1].id,scheduled_at=start)
    db.session.add(bracket);db.session.commit()
    rows=player_schedule_conflicts([players[0].id],start,60,viewer_id=players[0].id)
    assert {r['kind'] for r in rows}=={'league_match','tournament_match'}
    assert next(r for r in rows if r['kind']=='tournament_match')['timing']=='estimated'
    assert player_schedule_conflicts([players[0].id],start,60,exclude_league_match_id=league_match.id,
                                     exclude_tournament_id=tournament.id)==[]
    league_match.scheduled_at=None
    league_match.schedule_proposals='[{"scheduled_at":"2027-01-01T12:00:00Z"}]'
    bracket.result_state='void';db.session.commit()
    assert player_schedule_conflicts([players[0].id],start,60)==[]


def auth(player):
    from backend.routes.auth import _issue_token
    return {'Authorization': f'Bearer {_issue_token(player)}'}


def test_join_requires_current_review_and_does_not_take_a_place_before_acceptance(setup):
    app,players,court,start=setup
    existing=game_for(players[0],court,start)
    target=game_for(players[1],court,start+timedelta(minutes=15))
    target.visibility='open';db.session.commit()
    client=app.test_client();path=f'/api/games/{target.id}/join'
    response=client.post(path,headers=auth(players[0]),json={})
    assert response.status_code==409 and response.json['error']=='schedule_conflict'
    assert not GamePlayer.query.filter_by(game_id=target.id,user_id=players[0].id).first()
    token=response.json['schedule_conflict_token']
    existing.duration_minutes=120;db.session.commit()
    stale=client.post(path,headers=auth(players[0]),json={'schedule_conflict_ack':token})
    assert stale.status_code==409 and stale.json['schedule_conflict_token']!=token
    joined=client.post(path,headers=auth(players[0]),json={'schedule_conflict_ack':stale.json['schedule_conflict_token']})
    assert joined.status_code==200 and joined.json['is_joined'] is True
    assert client.post(path,headers=auth(players[0]),json={}).status_code==200


def test_host_creation_and_rescheduling_require_review_without_changing_existing_plan(setup):
    app,players,court,start=setup
    existing=game_for(players[0],court,start)
    client=app.test_client();headers=auth(players[0])
    payload={'court_id':court.id,'scheduled_at':start.isoformat(),'duration_minutes':60,
             'game_type':'casual','visibility':'open','max_players':4,
             'client_attempt_id':'conflict-create-attempt-001'}
    warning=client.post('/api/games',headers=headers,json=payload)
    assert warning.status_code==409 and Game.query.count()==1
    accepted={**payload,'schedule_conflict_ack':warning.json['schedule_conflict_token']}
    created=client.post('/api/games',headers=headers,json=accepted)
    assert created.status_code==201
    assert client.post('/api/games',headers=headers,json=payload).json['id']==created.json['id']
    new_start=start+timedelta(minutes=15)
    change={'scheduled_at':new_start.isoformat()}
    edit=client.patch(f'/api/games/{created.json["id"]}',headers=headers,json=change)
    assert edit.status_code==409
    assert db.session.get(Game,created.json['id']).scheduled_at==start
    applied=client.patch(f'/api/games/{created.json["id"]}',headers=headers,
        json={**change,'schedule_conflict_ack':edit.json['schedule_conflict_token']})
    assert applied.status_code==200
    assert db.session.get(Game,created.json['id']).scheduled_at==new_start
    assert db.session.get(Game,existing.id).scheduled_at==start


def test_waitlist_acceptance_keeps_the_held_place_until_conflict_is_reviewed(setup):
    from backend.models import GameWaitlist
    app,players,court,start=setup
    game_for(players[0],court,start)
    target=game_for(players[1],court,start+timedelta(minutes=15))
    target.visibility='open';target.max_players=2
    offered=GameWaitlist(game=target,user_id=players[0].id,offer_status='offered',
        offered_at=utcnow(),offer_expires_at=utcnow()+timedelta(hours=1))
    db.session.add(offered);db.session.commit()
    client=app.test_client();path=f'/api/games/{target.id}/waitlist/respond'
    warning=client.post(path,headers=auth(players[0]),json={'accept':True})
    assert warning.status_code==409 and GameWaitlist.query.count()==1
    assert len(target.players)==1
    response=client.post(path,headers=auth(players[0]),json={'accept':True,
        'schedule_conflict_ack':warning.json['schedule_conflict_token']})
    assert response.status_code==200 and response.json['is_joined']
    assert GameWaitlist.query.count()==0


def test_early_started_tournament_uses_actual_start_before_the_published_estimate(setup):
    _,players,court,start=setup
    tournament=Tournament(name='Early match',court_id=court.id,organizer_id=players[0].id,
                          starts_at=start,status='active',match_minutes=30)
    db.session.add(tournament);db.session.flush()
    entries=[TournamentEntry(tournament=tournament,player1_id=player.id) for player in players[:2]]
    db.session.add_all(entries);db.session.flush()
    match=TournamentMatch(tournament=tournament,round=1,position=1,
        entry1_id=entries[0].id,entry2_id=entries[1].id,scheduled_at=start+timedelta(hours=2),
        started_at=start,play_state='playing')
    db.session.add(match);db.session.commit()
    rows=player_schedule_conflicts([players[0].id],start,30,viewer_id=players[0].id)
    assert len(rows)==1 and rows[0]['timing']=='playing'
    assert rows[0]['starts_at'].startswith(start.isoformat())
    assert player_schedule_conflicts([players[0].id],start+timedelta(minutes=30),30)==[]


def test_following_date_edit_reviews_future_participant_before_any_change(setup):
    from backend.routes.games import _lock_stable_game_edit_scope
    from backend.models import GameRecurrenceRsvp
    app, players, court, start = setup
    client = app.test_client()
    created = client.post('/api/games', headers=auth(players[0]), json={
        'court_id': court.id, 'scheduled_at': start.isoformat(), 'duration_minutes': 60,
        'game_type': 'casual', 'max_players': 4, 'visibility': 'open',
        'recurrence': 'weekly', 'recurrence_timezone': 'UTC',
    })
    assert created.status_code == 201, created.json
    root = db.session.get(Game, created.json['id'])
    following = Game.query.filter(Game.recurrence_series_id == root.id, Game.id != root.id).order_by(Game.scheduled_at).first()
    original = following.scheduled_at
    db.session.add(GamePlayer(game=following, user_id=players[1].id))
    db.session.add(GameRecurrenceRsvp(game_id=root.id, user_id=players[2].id, standing_rsvp=True))
    db.session.commit()
    busy = game_for(players[1], court, original + timedelta(minutes=30))
    locked, _ = _lock_stable_game_edit_scope(root.id, players[0].id, following_dates=True)
    assert {person.id for person in locked} == {person.id for person in players}
    db.session.rollback()
    payload = {'scheduled_at': (start + timedelta(minutes=15)).isoformat(), 'edit_scope': 'following_dates'}
    path = f'/api/games/{root.id}'
    warning = client.patch(path, headers=auth(players[0]), json=payload)
    assert warning.status_code == 409, warning.json
    assert any(row['user_id'] == players[1].id and row['kind'] == 'busy' for row in warning.json['conflicts'])
    assert db.session.get(Game, following.id).scheduled_at == original
    assert db.session.get(Game, root.id).scheduled_at == start
    busy.scheduled_at += timedelta(minutes=5); db.session.commit()
    stale = client.patch(path, headers=auth(players[0]), json={**payload, 'schedule_conflict_ack': warning.json['schedule_conflict_token']})
    assert stale.status_code == 409 and stale.json['schedule_conflict_token'] != warning.json['schedule_conflict_token']
    accepted = client.patch(path, headers=auth(players[0]), json={**payload, 'schedule_conflict_ack': stale.json['schedule_conflict_token']})
    assert accepted.status_code == 200, accepted.json
    assert db.session.get(Game, following.id).scheduled_at == original + timedelta(minutes=15)


def test_new_recurrence_days_review_standing_players_but_not_invitees(setup):
    from backend.models import GameRecurrenceRsvp
    app, players, court, start = setup
    client = app.test_client()
    created = client.post('/api/games', headers=auth(players[0]), json={
        'court_id': court.id, 'scheduled_at': start.isoformat(), 'duration_minutes': 60,
        'game_type': 'casual', 'max_players': 4, 'visibility': 'open',
        'recurrence': 'weekly', 'recurrence_timezone': 'UTC',
    })
    assert created.status_code == 201, created.json
    root = db.session.get(Game, created.json['id'])
    db.session.add_all([GameRecurrenceRsvp(game_id=root.id,user_id=players[1].id,standing_rsvp=True),
                        GameRecurrenceRsvp(game_id=root.id,user_id=players[2].id,standing_rsvp=False)])
    next_day = start + timedelta(days=1)
    game_for(players[1],court,next_day); game_for(players[2],court,next_day)
    payload = {'edit_scope':'following_dates','recurrence_weekdays':[start.strftime('%a').lower(), next_day.strftime('%a').lower()]}
    warning = client.patch(f'/api/games/{root.id}',headers=auth(players[0]),json=payload)
    assert warning.status_code == 409, warning.json
    assert {row['user_id'] for row in warning.json['conflicts']} == {players[1].id}
    applied = client.patch(f'/api/games/{root.id}',headers=auth(players[0]),json={**payload,'schedule_conflict_ack':warning.json['schedule_conflict_token']})
    assert applied.status_code == 200, applied.json
    new_date = Game.query.filter_by(recurrence_series_id=root.id,scheduled_at=next_day).one()
    assert {row.user_id for row in new_date.players} == {players[0].id,players[1].id}
    assert players[2].id in {row.user_id for row in new_date.invites}


def test_personal_overlap_covers_later_pages_and_ignores_noncommitments(setup):
    from backend.models import GameInvite, GameWaitlist
    from backend.services.player_schedule import personal_schedule_overlaps
    app, players, court, start = setup
    for day in range(35):
        game_for(players[0],court,start+timedelta(days=day))
    last = start+timedelta(days=40)
    first=game_for(players[0],court,last)
    second=game_for(players[0],court,last+timedelta(minutes=30))
    unjoined=game_for(players[1],court,last)
    db.session.add_all([GameInvite(game=unjoined,user_id=players[0].id),GameWaitlist(game=unjoined,user_id=players[0].id)])
    db.session.commit()
    result=personal_schedule_overlaps(players[0].id)
    assert len(result)==1
    assert {plan['action_url'] for plan in result[0]['plans']}=={f'/#game/{first.id}',f'/#game/{second.id}'}
    home=app.test_client().get('/api/play/home',headers=auth(players[0]))
    assert home.status_code==200 and len(home.json['schedule_conflicts'])==1
    second.status='cancelled';db.session.commit()
    assert personal_schedule_overlaps(players[0].id)==[]
