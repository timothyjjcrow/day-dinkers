"""Late results change records only after a current, explicit effects review."""
import json
from datetime import timedelta

from test_league_round_operations import app, client, field, auth, preview, close, decisive_round, availability
from backend.app import db
from backend.models import LeagueMatch, CompetitionResultEvent, utcnow


def closed_field(client, app, final=False):
    people, league, url = field(client, app, count=3, total_rounds=1 if final else 3)
    match = league.matches[0]
    assert close(client, people, url, preview(client, people, url)).status_code == 200
    return people, league, match, url


def call(client, url, match, person, **body):
    return client.post(f'{url}/matches/{match.id}/closed-review', headers=auth(person),
        json={'expected_result_version':match.result_version, **body})


def requester(people, match):
    return next(p for p in people if p['user']['id'] == match.player1_id)


def test_late_claim_review_preserves_closure_and_future_draw_then_amends_record(client, app):
    people, league, match, url = closed_field(client, app)
    original = json.loads(league.round_history)[0]
    later = [m for m in league.matches if m.round == 2]
    later[0].scheduled_at = utcnow()+timedelta(days=1)
    db.session.commit()
    assignments = [(m.id,m.player1_id,m.player2_id,m.scheduled_at) for m in later]
    result = call(client,url,match,requester(people,match),action='request',score1=11,score2=7,reason='We played before the deadline.')
    assert result.status_code == 200, result.get_json()
    assert match.winner_id is None and league.member_for(match.player1_id).points == 0
    plan = call(client,url,match,people[0],action='preview').get_json()
    assert plan['later_match_count'] == 3 and plan['preserved_appointments'] == 1
    assert plan['season_changes'][0]['delta']['points'] == 3
    assert call(client,url,match,people[0],action='approve',preview_fingerprint=plan['preview_fingerprint'],reason='Checked both players.').status_code == 400
    approved = call(client,url,match,people[0],action='approve',preview_fingerprint=plan['preview_fingerprint'],reason='Checked both players.',acknowledge_downstream_effect=True)
    assert approved.status_code == 200, approved.get_json()
    assert league.member_for(match.player1_id).points == 3
    assert [(m.id,m.player1_id,m.player2_id,m.scheduled_at) for m in later] == assignments
    history = json.loads(league.round_history)
    assert history[0] == original and history[-1]['action'] == 'result_amended'
    assert history[-1]['round_standings'][0]['players'][0]['wins'] == 1
    assert call(client,url,match,people[0],action='approve',reason='again').status_code == 409


def test_late_review_objected_and_stale_downstream_preview_must_be_reviewed_again(client, app):
    people, league, match, url = closed_field(client, app)
    call(client,url,match,requester(people,match),action='request',score1=11,score2=7,reason='Late report')
    plan = call(client,url,match,people[0],action='preview').get_json()
    other = next(p for p in people if p['user']['id'] == match.player2_id)
    assert call(client,url,match,other,action='respond',agree=False,reason='The score was reversed.').status_code == 200
    assert match.winner_id is None
    assert call(client,url,match,people[0],action='approve',reason='checked',preview_fingerprint=plan['preview_fingerprint'],acknowledge_downstream_effect=True).status_code == 409
    plan = call(client,url,match,people[0],action='preview').get_json()
    later = next(m for m in league.matches if m.round == 2)
    later.schedule_version += 1; db.session.commit()
    assert call(client,url,match,people[0],action='approve',reason='checked',preview_fingerprint=plan['preview_fingerprint'],acknowledge_downstream_effect=True).status_code == 409
    fresh = call(client,url,match,people[0],action='preview').get_json()
    rejected = call(client,url,match,people[0],action='reject',reason='Both players disagree; original record remains.',preview_fingerprint=fresh['preview_fingerprint'])
    assert rejected.status_code == 200 and match.winner_id is None
    assert json.loads(match.closed_round_review)['status'] == 'rejected'
    assert len(json.loads(league.round_history)) == 1


def test_final_round_review_previews_title_and_can_correct_twice_without_double_count(client, app):
    people, league, match, url = closed_field(client, app, final=True)
    person = requester(people,match)
    for a,b in ((11,7),(7,11)):
        assert call(client,url,match,person,action='request',score1=a,score2=b,reason='Verified late result').status_code == 200
        plan = call(client,url,match,people[0],action='preview').get_json()
        expected = match.player1_id if a>b else match.player2_id
        assert plan['champion_after']['id'] == expected
        assert call(client,url,match,people[0],action='approve',reason='Players and record checked.',preview_fingerprint=plan['preview_fingerprint'],acknowledge_downstream_effect=True).status_code == 200
        assert league.champion_user_id == expected
    assert league.member_for(match.player1_id).wins == 0 and league.member_for(match.player1_id).losses == 1
    assert league.member_for(match.player2_id).wins == 1
    assert CompetitionResultEvent.query.filter_by(action='late_review_approved').count() == 2


def test_missing_historical_membership_does_not_crash_absence_undo_or_restore_invalid_match(client, app):
    people, league, url = field(client,app,count=3)
    match = league.matches[0]
    person = requester(people,match)
    availability(client,person,url,'unavailable')
    removed = league.member_for(match.player2_id)
    league.members.remove(removed); db.session.delete(removed); db.session.commit()
    data = availability(client,person,url,'available')
    assert data['my_unavailable_round'] is None
    assert match.effective_result_state() == 'void' and match.scheduled_at is None


def test_late_review_permissions_attention_and_private_evidence(client, app):
    people, league, match, url = closed_field(client, app)
    outsider = next(p for p in people if p['user']['id'] not in (match.player1_id,match.player2_id,league.organizer_id))
    assert call(client,url,match,outsider,action='request',score1=11,score2=7,reason='Not my match').status_code == 403
    assert call(client,url,match,requester(people,match),action='request',score1=11,score2=7,reason='Private player evidence').status_code == 200
    host_detail=client.get(url,headers=auth(people[0])).get_json()
    assert host_detail['closed_review_action_count'] == 1
    outsider_detail=client.get(url+f'?match_id={match.id}',headers=auth(outsider)).get_json()
    public_match=next(m for m in outsider_detail['matches'] if m['id']==match.id)
    assert public_match['closed_round_review'] == {} and public_match['result_history'] == []
    assert call(client,url,match,outsider,action='preview').status_code == 403
    plan=call(client,url,match,people[0],action='preview').get_json()
    call(client,url,match,people[0],action='approve',reason='Private reviewed evidence',preview_fingerprint=plan['preview_fingerprint'],acknowledge_downstream_effect=True)
    data=client.get(url,headers=auth(outsider)).get_json()
    assert 'reason' not in data['round_history'][-1]
