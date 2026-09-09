"""Disposable in-memory tournament browser fixture; synthetic accounts only."""
import os
import re
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
os.environ.update(APP_ENV='testing', DATABASE_URL='sqlite:///:memory:', TEST_DATABASE_URL='sqlite:///:memory:',
                  PUSH_DELIVERY_ENABLED='false', AUTO_SEED_COURTS='false')
from flask import jsonify, request
from backend.app import create_app, db
from backend.models import Court, Tournament, TournamentEntry, TournamentMatch, TournamentWaitlist, User, League, LeagueMember, LeagueMatch, utcnow

app = create_app('testing')

@app.after_request
def source_assets(response):
    if request.path == '/':
        response.direct_passthrough = False
        page = response.get_data(as_text=True)
        page = re.sub(r'/release-assets/r\d+/([\w-]+)\.min\.js', r'/\1.js', page)
        page = re.sub(r'/release-assets/r\d+/styles-v15\.min\.css', '/styles-v15.css', page)
        response.set_data(page)
    response.headers['Cache-Control'] = 'no-store'
    return response

with app.app_context():
    db.create_all()
    now = utcnow()
    court = Court(name='Sunset Pickleball Courts', city='Irvine', state='CA', latitude=33.68, longitude=-117.82, num_courts=4)
    db.session.add(court); db.session.flush()
    people=[]
    for index,name in enumerate(('Jamie Organizer','Alex Chen','Sam Rivera','Jordan Lee','Casey Patel','Taylor Kim','Morgan Diaz','Riley Park','Drew Brooks')):
        person=User(email=f'tournament-{index}@example.test',display_name=name,email_verified_at=now,
                    onboarding_completed_at=now,home_court_id=court.id,home_area='Irvine, CA',home_lat=court.latitude,
                    home_lng=court.longitude,skill_rating=3.5,skill_level='intermediate',availability='["sat-am"]',
                    bio='Synthetic local browser fixture.')
        person.set_password('tournament-test-only');db.session.add(person);people.append(person)
    db.session.flush()
    tournament=Tournament(name='Sunset Doubles',court=court,organizer=people[0],starts_at=now+timedelta(minutes=30),
                          event_type='doubles',format='single_elim',max_entries=8,court_count=2,match_minutes=30,
                          rest_minutes=5,entry_fee_cents=1200,payment_method='Pay at check-in',withdrawal_policy='Full refund before play begins.')
    db.session.add(tournament);db.session.flush()
    for index in range(1,9,2):
        db.session.add(TournamentEntry(tournament=tournament,player1=people[index],player2=people[index+1],partner_status='accepted'))
    db.session.commit()
    league=League(name='Sunset Singles League', court=court,organizer=people[0],starts_at=now,
        status='active',current_round=1,total_rounds=2,round_started_at=now,round_days=7)
    db.session.add(league)
    for index, person in enumerate(people[:6]):
        db.session.add(LeagueMember(league=league,user=person,box=1+index//3))
    db.session.flush()
    from backend.routes.leagues import _generate_round
    _generate_round(league)
    db.session.flush()
    for match in league.matches:
        if {match.player1_id,match.player2_id} == {people[1].id,people[2].id}:
            match.scheduled_at=now+timedelta(days=1)
            match.scheduled_court=court
    db.session.commit()
    fixture_ids={'tournament':tournament.id,'organizer':people[0].id,'player':people[1].id}
    queue_event = Tournament(name='Sunset Singles Open',court=court,organizer=people[0],starts_at=now+timedelta(days=2),
        event_type='singles',format='single_elim',max_entries=2,entry_fee_cents=0,
        payment_method='No payment needed',withdrawal_policy='Leave before the bracket starts.')
    db.session.add(queue_event)
    for person in people[1:3]:
        db.session.add(TournamentEntry(tournament=queue_event,player1=person,partner_status='accepted'))
    db.session.commit()
    fixture_ids['waitlist_tournament'] = queue_event.id
    doubles_queue=Tournament(name='Doubles Recovery Open',court=court,organizer=people[0],starts_at=now+timedelta(days=3),
        event_type='doubles',format='single_elim',max_entries=2,entry_fee_cents=500,payment_method='Pay at check-in',
        withdrawal_policy='Leave before the bracket starts; ask organizer about refunds.')
    db.session.add(doubles_queue)
    for a,b in ((1,2),(3,4)):
        db.session.add(TournamentEntry(tournament=doubles_queue,player1=people[a],player2=people[b],partner_status='accepted'))
    db.session.commit();fixture_ids['doubles_recovery']=doubles_queue.id
    if os.environ.get('TOURNAMENT_E2E_RECOVERY')=='1':
        doubles_queue.entries.remove(doubles_queue.entries[0])
        invited=TournamentEntry(tournament=doubles_queue,player1=people[0],partner_invitee=people[5],
            partner_status='pending',partner_pending_on='invitee',partner_response_deadline_at=now+timedelta(hours=24))
        db.session.add(invited);db.session.flush()
        from backend.routes.tournaments import _partner_event
        _partner_event(invited,'invited',people[0].id)
        for index in range(2):
            extra=Tournament(name=f'Upcoming singles {index+1}',court=court,organizer=people[0],status='active',
                starts_at=now+timedelta(days=5+index),event_type='singles',format='single_elim')
            db.session.add(extra);db.session.flush()
            first=TournamentEntry(tournament=extra,player1=people[0]);second=TournamentEntry(tournament=extra,player1=people[1+index])
            db.session.add_all([first,second]);db.session.flush()
            db.session.add(TournamentMatch(tournament=extra,entry1=first,entry2=second,round=1,position=0,
                scheduled_at=extra.starts_at,court_number=1))
        db.session.commit()

@app.get('/__tournament_fixture')
def fixture():
    return jsonify(fixture_ids)

@app.route('/__recovery-clock',methods=['GET','POST'])
def recovery_clock():
    """Visible fixture controls advance only synthetic consent deadlines."""
    if request.method=='POST':
        event=db.session.get(Tournament,fixture_ids['doubles_recovery'])
        if request.form.get('action')=='offer':
            row=next(r for r in event.waitlist if r.user_id==1)
            row.expires_at=utcnow()-timedelta(seconds=1)
            if not next((r for r in event.waitlist if r.user_id==9),None):
                db.session.add(TournamentWaitlist(tournament=event,user_id=9,status='queued'))
        elif request.form.get('action')=='partner':
            event.entry_for(1).partner_response_deadline_at=utcnow()-timedelta(seconds=1)
        db.session.commit()
        from backend.routes.tournaments import maintain_tournament_waitlists
        maintain_tournament_waitlists()
    return '<title>Synthetic recovery clock</title><h1>Local fixture controls</h1><p>Only in-memory test data changes.</p><form method="post"><button name="action" value="offer">Expire Jamie’s place offer and queue Drew</button><button name="action" value="partner">Expire Jamie’s partner invitation</button></form><a href="/#tournament/3">Return to doubles tournament</a>'

@app.get('/__mobile-frame')
def mobile_frame():
    return '''<!doctype html><title>390px tournament viewport fixture</title>
    <body style="margin:8px;display:flex;align-items:flex-start;gap:16px">
    <iframe title="Mobile tournament" src="/#tournament/1" style="width:390px;height:640px;border:0;display:block;flex:none"></iframe>
    <pre id="layout-metrics" style="white-space:pre-wrap">Measuring responsive layout…</pre>
    <script>const frame=document.querySelector('iframe');setInterval(()=>{
      const doc=frame.contentDocument,card=doc.querySelector('.tournament-personal-match');if(!card)return;
      document.querySelector('#layout-metrics').textContent=JSON.stringify({viewport:frame.contentWindow.innerWidth,
      documentWidth:doc.documentElement.scrollWidth,personalMatchTop:Math.round(card.getBoundingClientRect().top),
      personalMatchBottom:Math.round(card.getBoundingClientRect().bottom),buttons:[...card.querySelectorAll('button')].map(b=>({label:b.textContent.trim(),height:Math.round(b.getBoundingClientRect().height)}))},null,2);
    },1000);</script>'''

@app.get('/__league-mobile-frame')
def league_mobile_frame():
    return mobile_frame().replace('/#tournament/1','/#league/1').replace('.tournament-personal-match','.league-personal-match').replace('Mobile tournament','Mobile league')

if __name__ == '__main__':
    if os.environ.get('TOURNAMENT_E2E_ACTIVE') == '1':
        with app.test_client() as client:
            token=client.post('/api/auth/login',json={'email':'tournament-0@example.test','password':'tournament-test-only'}).get_json()['token']
            headers={'Authorization':f'Bearer {token}'}
            started=client.post('/api/tournaments/1/start',headers=headers,json={})
            assert started.status_code == 200, started.get_json()
            first_match=next(match['id'] for match in started.get_json()['matches'] if match['entry1_id'] and match['entry2_id'])
            assert client.post(f'/api/tournaments/1/matches/{first_match}/play-state',headers=headers,json={'play_state':'called'}).status_code == 200
            assert client.post(f'/api/tournaments/1/matches/{first_match}/play-state',headers=headers,json={'play_state':'playing'}).status_code == 200
    app.run(host='127.0.0.1',port=int(os.environ.get('TOURNAMENT_E2E_PORT','8056')),threaded=False,debug=False,use_reloader=False)
