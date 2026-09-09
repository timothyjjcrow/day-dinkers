"""Disposable UI fixture for rank 66 and pagination; standings are synthetic."""
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
os.environ.update(APP_ENV='testing', TEST_DATABASE_URL='sqlite:///:memory:',
                  DATABASE_URL='sqlite:///:memory:', AUTO_SEED_COURTS='false',
                  PUSH_DELIVERY_ENABLED='false')
from flask import request
from backend.app import create_app, db
from backend.models import Court, User, utcnow

app = create_app('testing')


@app.after_request
def readable_assets(response):
    if request.path == '/':
        response.direct_passthrough = False
        html = re.sub(r'/release-assets/r\d+/([\w-]+)\.min\.(js|css)',
                      r'/\1.\2', response.get_data(as_text=True))
        response.set_data(html)
    response.headers['Cache-Control'] = 'no-store'
    return response


with app.app_context():
    db.create_all()
    court = Court(name='Synthetic Ranking Courts', city='Portland', state='OR',
                  latitude=45.5, longitude=-122.7, num_courts=4)
    db.session.add(court)
    db.session.flush()
    for n in range(66):
        player = User(email=f'ranking-{n}@example.test',
                      display_name='Alex Ranking Check' if n == 0 else f'Synthetic Player {n:02}',
                      rating=700 if n == 0 else 1000+n, ranked_wins=0 if n == 0 else 1,
                      ranked_losses=1 if n == 0 else 0,
                      home_court_id=court.id, home_area='Portland, OR',
                      home_lat=45.5, home_lng=-122.7, last_lat=45.5, last_lng=-122.7,
                      skill_level='intermediate', skill_rating=3.5,
                      onboarding_completed_at=utcnow(), email_verified_at=utcnow())
        player.set_password('local-ranking-test')
        db.session.add(player)
    db.session.commit()

print('RANKINGS READY: ranking-0@example.test / local-ranking-test, 66 synthetic players', flush=True)
app.run(host='127.0.0.1', port=8054, use_reloader=False, threaded=False)
