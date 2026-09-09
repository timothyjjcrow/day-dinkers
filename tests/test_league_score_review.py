"""Unusual scores need deliberate confirmation without changing recorded points."""
from test_league_round_operations import app, client, field, auth, preview, close
from backend.models import CompetitionResultEvent


def test_unusual_current_score_is_not_saved_until_explicitly_confirmed(client,app):
    people,league,url=field(client,app,count=3)
    match=league.matches[0]
    actor=next(person for person in people if person['user']['id']==match.player1_id)
    payload={'score1':5,'score2':0,'result_version':match.result_version}
    path=f'{url}/matches/{match.id}/score'
    response=client.post(path,headers=auth(actor),json=payload)
    assert response.status_code==422 and response.get_json()['can_confirm']
    assert match.score1 is None and match.effective_result_state()=='unreported'
    assert CompetitionResultEvent.query.count()==0
    accepted=client.post(path,headers=auth(actor),json={**payload,'accept_nonstandard_score':True})
    assert accepted.status_code==200,accepted.get_json()
    assert (match.score1,match.score2)==(5,0) and match.effective_result_state()=='awaiting_confirmation'
    opponent=next(person for person in people if person['user']['id']==match.player2_id)
    confirmed=client.post(f'{url}/matches/{match.id}/confirm',headers=auth(opponent),json={'result_version':match.result_version})
    assert confirmed.status_code==200,confirmed.get_json()
    assert league.member_for(match.player1_id).wins==1


def test_closed_review_unusual_score_requires_ack_before_request_exists(client,app):
    people,league,url=field(client,app,count=3)
    match=league.matches[0]
    assert close(client,people,url,preview(client,people,url)).status_code==200
    actor=next(person for person in people if person['user']['id']==match.player1_id)
    path=f'{url}/matches/{match.id}/closed-review'
    payload={'action':'request','score1':10,'score2':9,'reason':'A timed game ended at the whistle.','result_version':match.result_version}
    assert client.post(path,headers=auth(actor),json=payload).status_code==422
    assert match.closed_round_review=='{}'
    assert client.post(path,headers=auth(actor),json={**payload,'accept_nonstandard_score':True}).status_code==200
    assert match.winner_id is None
