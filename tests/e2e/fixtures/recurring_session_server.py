"""Disposable, local-only recurring-session browser fixture (no real accounts)."""
import os
import re
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
os.environ.update(
    APP_ENV='testing', DATABASE_URL='sqlite:///:memory:',
    TEST_DATABASE_URL='sqlite:///:memory:', PUSH_DELIVERY_ENABLED='false',
    AUTO_SEED_COURTS='false',
)
from flask import jsonify, request
from backend.app import create_app, db
from backend.models import Court, Game, GamePlayer, GameWaitlist, User, utcnow
from backend.routes.games import _materialize_series, roll_forward_recurring, _promote_from_waitlist

app = create_app('testing')

@app.after_request
def source_assets(response):
    if request.path == '/':
        response.direct_passthrough = False
        text = response.get_data(as_text=True)
        text = re.sub(r'/release-assets/r\d+/([\w-]+)\.min\.js', r'/\1.js', text)
        text = re.sub(r'/release-assets/r\d+/styles-v15\.min\.css', '/styles-v15.css', text)
        response.set_data(text)
    response.headers['Cache-Control'] = 'no-store'
    return response

with app.app_context():
    db.create_all()
    now = utcnow()
    court = Court(name='Recurring Test Court', city='Irvine', state='CA',
                  latitude=33.68, longitude=-117.82, num_courts=8)
    db.session.add(court)
    db.session.flush()
    people = []
    for number, name in enumerate(('Jamie Host', 'Casey Player', 'Morgan Player')):
        person = User(
            email=f'recurrence-{number}@example.test', display_name=name,
            email_verified_at=now, onboarding_completed_at=now,
            home_court_id=court.id, home_area='Irvine, CA',
            home_lat=court.latitude, home_lng=court.longitude,
            skill_level='intermediate', skill_rating=3.5,
            availability='["sat-am"]', bio='Local synthetic test account.',
        )
        person.set_password('recurrence-test-only')
        db.session.add(person)
        people.append(person)
    db.session.flush()
    series = Game(
        court_id=court.id, creator_id=people[0].id,
        scheduled_at=now - timedelta(days=1), title='Saturday regulars',
        game_type='casual', visibility='open', max_players=8,
        recurrence='weekly', recurrence_timezone='America/Los_Angeles',
        duration_minutes=90,
    )
    active = Game(
        court_id=court.id, creator_id=people[0].id,
        scheduled_at=now - timedelta(hours=2), title='Today’s regulars',
        game_type='casual', visibility='open', max_players=8,
        recurrence='weekly', recurrence_timezone='America/Los_Angeles',
        duration_minutes=60,
    )
    db.session.add_all([series, active])
    db.session.flush()
    for game in (series, active):
        db.session.add_all(GamePlayer(game=game, user=person) for person in people)
    db.session.flush()
    _materialize_series(series)
    _materialize_series(active)
    series.status = 'completed'
    series.completed_at = series.scheduled_at + timedelta(minutes=90)
    db.session.commit()
    roll_forward_recurring()
    fixture_ids = {'past': series.id, 'ended': active.id, 'host': people[0].id, 'player': people[1].id}
    fixture_ids['dates'] = [row.id for row in Game.query.filter_by(
        recurrence_series_id=series.id,
    ).order_by(Game.scheduled_at).all()]
    handoff = Game(court=court, creator=people[0], scheduled_at=now+timedelta(days=1),
                   title='Evening doubles', game_type='casual', visibility='open', max_players=4)
    offer = Game(court=court, creator=people[0], scheduled_at=now+timedelta(days=2),
                 title='A spot for Morgan', game_type='casual', visibility='open', max_players=2)
    db.session.add_all([handoff, offer]); db.session.flush()
    db.session.add_all([GamePlayer(game=handoff, user=people[0]), GamePlayer(game=handoff, user=people[1]),
                       GamePlayer(game=offer, user=people[0]), GameWaitlist(game=offer, user=people[2])])
    db.session.flush(); _promote_from_waitlist(offer); db.session.commit()
    fixture_ids.update(handoff=handoff.id, offer=offer.id)

@app.get('/__recurrence_fixture')
def fixture():
    return jsonify(fixture_ids)

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=int(os.environ.get('RECURRENCE_E2E_PORT', '8041')), threaded=False, debug=False, use_reloader=False)
