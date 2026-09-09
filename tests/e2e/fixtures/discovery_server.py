"""Disposable discovery/agenda browser fixture. All accounts and play are synthetic."""
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
os.environ.update(APP_ENV='testing',TEST_DATABASE_URL='sqlite:///:memory:',
    DATABASE_URL='sqlite:///:memory:',AUTO_SEED_COURTS='false',PUSH_DELIVERY_ENABLED='false')
from flask import request
from backend.app import create_app, db
from backend.models import Court, User, Game, GamePlayer, GameWaitlist, GameHostHandoff, notify, utcnow

app=create_app('testing')

@app.after_request
def source_assets(response):
    if request.path == '/':
        response.direct_passthrough=False
        response.set_data(re.sub(r'/release-assets/r\d+/([\w-]+)\.min\.(js|css)',
            r'/\1.\2',response.get_data(as_text=True)))
    response.headers['Cache-Control']='no-store'
    return response

with app.app_context():
    db.create_all()
    players=[]
    for n,name in enumerate(['Alex Discovery','Sam Rivera','Casey Brooks','Morgan Chen']):
        user=User(email=f'discovery-{n}@example.test',display_name=name,
            onboarding_completed_at=utcnow(),email_verified_at=utcnow(),
            skill_level='beginner',skill_rating=2.5)
        user.set_password('local-discovery-test')
        db.session.add(user);players.append(user)
    courts=[Court(name='Cedar Park Courts',city='Portland',state='OR',address='100 Demo Lane',
        latitude=45.5,longitude=-122.7,num_courts=4),
        Court(name='Riverside Indoor Courts',city='Portland',state='OR',address='200 Demo Lane',
        latitude=45.6,longitude=-122.6,num_courts=2)]
    db.session.add_all(courts);db.session.flush()
    tomorrow=datetime.now(ZoneInfo('America/Los_Angeles')).date()+timedelta(days=1)
    def starts(clock):
        return datetime.fromisoformat(f'{tomorrow}T{clock}').replace(tzinfo=ZoneInfo('America/Los_Angeles')).astimezone(timezone.utc).replace(tzinfo=None)
    def session(title,clock,*,court=0,capacity=4,joined=(),level=2.5):
        game=Game(title=title,creator_id=players[1].id,court_id=courts[court].id,
            scheduled_at=starts(clock),duration_minutes=90,game_type='casual',
            visibility='open',max_players=capacity,level_min=level,level_max=3,
            cost_cents=0)
        db.session.add(game);db.session.flush()
        for person in [players[1],*[players[n] for n in joined]]:
            db.session.add(GamePlayer(game_id=game.id,user_id=person.id,attending_at=utcnow()))
        return game
    session('Early beginner play','17:30')
    session('Doubles at six','18:00')
    session('Full doubles','18:15',capacity=2,joined=(2,))
    held=session('Waitlist offer session','18:30',capacity=2)
    db.session.add(GameWaitlist(game_id=held.id,user_id=players[0].id,offer_status='offered',
        offered_at=utcnow(),offer_expires_at=utcnow()+timedelta(minutes=25)))
    handoff=session('Host request session','18:45',joined=(0,))
    handoff_request=GameHostHandoff(game_id=handoff.id,requested_by_id=players[1].id,
        target_user_id=players[0].id,scope='this_date',status='pending',leave_on_accept=True,
        expires_at=utcnow()+timedelta(hours=4))
    db.session.add(handoff_request);db.session.flush()
    notify(players[0].id,'game_waitlist_offer','A spot is held for you',related_game_id=held.id,action_url=f'/#game/{held.id}')
    notify(players[0].id,'game_host_handoff','Sam asked you to host',related_game_id=handoff.id,action_url=f'/#game/{handoff.id}',unread_dedupe_key=f'game-handoff:{handoff_request.id}')
    session('Late evening play','19:30')
    session('Riverside evening','18:00',court=1)
    db.session.commit()
port=int(os.environ.get('DISCOVERY_TEST_PORT', '8055'))
print(f'DISCOVERY READY port{port} date{tomorrow}: discovery-0@example.test / local-discovery-test',flush=True)
app.run(host='127.0.0.1',port=port,use_reloader=False,threaded=False)
