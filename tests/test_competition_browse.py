"""Discovery filters act before pagination; private next-match data stays personal."""
from datetime import timedelta
from test_competition_pagination import app, client, register, headers, collect_pages
from backend.app import db
from backend.models import Court, Tournament, League, LeagueMember, LeagueMatch, utcnow


def test_tournament_filters_page_the_matching_catalog(client,app):
    user=register(client);now=utcnow()
    court=Court(name='Browse court',city='Test',state='CA',latitude=33,longitude=-117)
    db.session.add(court);db.session.flush()
    wanted=[]
    for index in range(12):
        event=Tournament(name=f'Event {index}',court=court,organizer_id=user['user']['id'],
            starts_at=now+timedelta(days=index),event_type='doubles' if index%2==0 else 'singles',
            status='registration' if index!=6 else 'active',division_min_rating=3,division_max_rating=3.9)
        db.session.add(event);db.session.flush()
        if index in (2,4):wanted.append(event.id)
    db.session.commit()
    path='/api/tournaments?lat=33&lng=-117&signup=1&event_type=doubles&self_rating=3.5&starts_after='+ (now+timedelta(days=1)).isoformat()+'Z&starts_before='+(now+timedelta(days=7)).isoformat()+'Z'
    rows,pages=collect_pages(client,path,headers(user),limit=1)
    assert [row['id'] for row in rows]==wanted and len(pages)==2
    assert client.get(path.replace('self_rating=3.5','self_rating=4.5'),headers=headers(user)).get_json()['items']==[]
    assert client.get('/api/tournaments?lat=33&lng=-117&starts_after=nope',headers=headers(user)).status_code==400


def test_league_filters_keep_open_skill_and_singles_honest(client,app):
    user=register(client)
    court=Court(name='League court',latitude=33,longitude=-117);db.session.add(court);db.session.flush()
    db.session.add(League(name='Open singles',court=court,organizer_id=user['user']['id'],starts_at=utcnow()+timedelta(days=2)))
    db.session.commit()
    base='/api/leagues?lat=33&lng=-117&signup=1&self_rating=4.5'
    assert len(client.get(base+'&event_type=singles',headers=headers(user)).get_json()['items'])==1
    assert client.get(base+'&event_type=doubles',headers=headers(user)).get_json()['items']==[]
    assert client.get(base+'&event_type=unknown',headers=headers(user)).status_code==400


def test_personal_match_summary_names_opponent_and_is_not_public(client,app):
    account=register(client)
    from backend.models import User
    other=User(email='other@browse.test',display_name='Sam Rivera');other.set_password('local-test-only');db.session.add(other)
    court=Court(name='Summary court',latitude=33,longitude=-117);db.session.add(court);db.session.flush()
    league=League(name='Personal league',court=court,organizer=other,status='active',current_round=1,starts_at=utcnow())
    db.session.add(league);db.session.flush()
    db.session.add(LeagueMember(league=league,user_id=account['user']['id'],box=1))
    db.session.add(LeagueMember(league=league,user=other,box=1))
    match=LeagueMatch(league=league,round=1,box=1,player1_id=account['user']['id'],player2=other)
    db.session.add(match);db.session.commit()
    personal=client.get('/api/leagues?mine=1',headers=headers(account)).get_json()['items'][0]['personal_match']
    assert personal['opponent']=='Sam Rivera' and personal['id']==match.id and personal['timing']=='needs_time'
    from backend.routes.leagues import _league_payload
    assert _league_payload(league,999)['personal_match'] is None


def test_next_match_is_independent_of_mine_history_pagination(client,app):
    from backend.models import User, TournamentEntry, TournamentMatch
    account=register(client); user=db.session.get(User,account['user']['id'])
    other=User(email='opponent@browse.test',display_name='Next Opponent');other.set_password('local-test-only')
    court=Court(name='Next court',latitude=33,longitude=-117);db.session.add_all([other,court]);db.session.flush()
    event=Tournament(name='Older active event',court=court,organizer=other,status='active',starts_at=utcnow()-timedelta(days=1))
    db.session.add(event);db.session.flush()
    a=TournamentEntry(tournament=event,player1=user);b=TournamentEntry(tournament=event,player1=other)
    db.session.add_all([a,b]);db.session.flush()
    match=TournamentMatch(tournament=event,round=1,position=0,entry1=a,entry2=b,scheduled_at=utcnow()+timedelta(minutes=20))
    db.session.add(match)
    for index in range(35):
        db.session.add(Tournament(name=f'Newer history {index}',court=court,organizer=user,status='completed',starts_at=utcnow()+timedelta(days=index+1)))
    db.session.commit()
    first=client.get('/api/tournaments?mine=1',headers=headers(account)).get_json()
    assert first['has_more'] and event.id not in [row['id'] for row in first['items']]
    rows=client.get('/api/competitions/next-matches',headers=headers(account)).get_json()['items']
    assert len(rows)==1 and rows[0]['item']['id']==event.id
    assert rows[0]['item']['personal_match']['opponent']=='Next Opponent'
    assert client.get('/api/competitions/next-matches').status_code==401
    match_ids={match.id}
    for index in range(4):
        another=Tournament(name=f'Active {index}',court=court,organizer=other,status='active',starts_at=utcnow())
        db.session.add(another);db.session.flush()
        one=TournamentEntry(tournament=another,player1=user);two=TournamentEntry(tournament=another,player1=other)
        db.session.add_all([one,two]);db.session.flush()
        next_match=TournamentMatch(tournament=another,round=1,position=0,entry1=one,entry2=two,scheduled_at=utcnow()+timedelta(hours=index+1))
        db.session.add(next_match);db.session.flush();match_ids.add(next_match.id)
    db.session.commit()
    first=client.get('/api/competitions/next-matches',headers=headers(account)).get_json()
    assert len(first['items'])==3 and first['total']==5 and first['has_more']
    all_rows,pages=collect_pages(client,'/api/competitions/next-matches',headers(account),limit=2)
    assert {row['item']['personal_match']['id'] for row in all_rows}==match_ids
    assert len(all_rows)==5 and len(pages)==3
