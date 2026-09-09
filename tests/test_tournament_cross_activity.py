"""External plans are reviewed separately from blocking event court conflicts."""
from datetime import timedelta
from test_tournament_operations import app, client, field, start, auth
from backend.app import db
from backend.models import Game,GamePlayer,Tournament,TournamentMatch,utcnow


def busy(tournament, uid, when):
    game=Game(court_id=tournament.court_id,creator_id=uid,scheduled_at=when,duration_minutes=90,
        status='upcoming',title='Private practice',visibility='private')
    db.session.add(game);db.session.flush();db.session.add(GamePlayer(game_id=game.id,user_id=uid));db.session.commit()
    return game


def test_start_reviews_external_commitments_before_creating_bracket(client,app):
    host,players,url=field(client,app,count=2)
    tournament=Tournament.query.one()
    game=busy(tournament,players[0]['user']['id'],tournament.starts_at)
    preview=client.get(url+'/preview',headers=auth(host)).get_json()
    payload={'preview_fingerprint':preview['preview_fingerprint']}
    blocked=client.post(url+'/start',headers=auth(host),json=payload)
    assert blocked.status_code==409
    data=blocked.get_json();assert data['conflicts'][0]['title']=='Another commitment'
    assert data['conflicts'][0]['action_url'] is None
    assert TournamentMatch.query.count()==0 and tournament.status=='registration'
    payload['schedule_conflict_ack']=data['schedule_conflict_token']
    game.scheduled_at+=timedelta(minutes=1);db.session.commit()
    stale=client.post(url+'/start',headers=auth(host),json=payload)
    assert stale.status_code==409
    payload['schedule_conflict_ack']=stale.get_json()['schedule_conflict_token']
    assert client.post(url+'/start',headers=auth(host),json=payload).status_code==200
    assert TournamentMatch.query.count()==1


def test_delay_and_call_require_current_external_review(client,app):
    host,players,url=field(client,app,count=2)
    start(client,host,url)
    tournament=Tournament.query.one();match=TournamentMatch.query.one()
    busy(tournament,players[0]['user']['id'],match.scheduled_at+timedelta(minutes=15))
    body={'minutes':15,'expected_schedule_version':tournament.schedule_version}
    preview=client.post(url+'/schedule/delay',headers=auth(host),json={**body,'preview':True})
    assert preview.status_code==200
    before=match.scheduled_at
    blocked=client.post(url+'/schedule/delay',headers=auth(host),json=body).get_json()
    assert blocked['error']=='schedule_conflict' and match.scheduled_at==before
    changed=client.post(url+'/schedule/delay',headers=auth(host),json={**body,'schedule_conflict_ack':blocked['schedule_conflict_token']})
    assert changed.status_code==200 and match.scheduled_at==before+timedelta(minutes=15)
    busy(tournament,players[0]['user']['id'],utcnow())
    body={'play_state':'called','expected_schedule_version':tournament.schedule_version}
    blocked=client.post(url+f'/matches/{match.id}/play-state',headers=auth(host),json=body).get_json()
    assert blocked['error']=='schedule_conflict' and match.play_state=='estimated'
    called=client.post(url+f'/matches/{match.id}/play-state',headers=auth(host),json={**body,'schedule_conflict_ack':blocked['schedule_conflict_token']})
    assert called.status_code==200 and match.play_state=='called'
