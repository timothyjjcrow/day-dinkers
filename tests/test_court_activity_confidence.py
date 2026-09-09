"""Court reports and check-in samples never imply a verified crowd forecast."""
from datetime import timedelta
import json

from test_business_governance import app, client, register, auth
from backend.app import db
from backend.models import CheckIn, Court, CourtCondition, utcnow


def test_small_activity_sample_is_counted_without_a_popularity_claim(app, client):
    users = [register(client, f'sample-{i}@example.test') for i in range(3)]
    with app.app_context():
        court = db.session.get(Court, 1)
        court.structured_hours = json.dumps({'timezone':'America/Chicago'})
        for index in range(9):
            at = utcnow()-timedelta(days=index+1)
            db.session.add(CheckIn(court_id=1,user_id=users[index%3]['user']['id'],checked_in_at=at,checked_out_at=at+timedelta(hours=1)))
        db.session.commit()
    first = client.get('/api/courts/1').get_json()['checkin_history']
    assert first['sample_size'] == 9 and first['unique_players'] == 3
    assert not first['sufficient_sample'] and first['windows'] == []
    assert first['timezone'] == 'America/Chicago'
    with app.app_context():
        at = utcnow()-timedelta(days=11)
        db.session.add(CheckIn(court_id=1,user_id=users[0]['user']['id'],checked_in_at=at,checked_out_at=at+timedelta(hours=1)))
        db.session.commit()
    assert client.get('/api/courts/1').get_json()['checkin_history']['sufficient_sample'] is True


def test_contradictory_recent_reports_remain_visible_without_anonymous_identities(app, client):
    wet = register(client, 'wet-report@example.test', name='Wet reporter')
    dry = register(client, 'dry-report@example.test', name='Dry reporter')
    assert client.post('/api/courts/1/condition', json={'condition':'wet'}, headers=auth(wet['token'])).status_code == 201
    assert client.post('/api/courts/1/condition', json={'condition':'good'}, headers=auth(dry['token'])).status_code == 201
    reports = client.get('/api/courts/1').get_json()['condition_reports']
    assert [row['condition'] for row in reports] == ['good','wet']
    assert all(row['user_name'] == 'Player' and 'user_id' not in row for row in reports)
    with app.app_context():
        for row in CourtCondition.query.filter_by(user_id=wet['user']['id']):
            row.created_at = utcnow()-timedelta(hours=4)
        db.session.commit()
    assert len(client.get('/api/courts/1').get_json()['condition_reports']) == 1


def test_updated_report_from_one_player_does_not_look_like_a_disagreement(client):
    user = register(client, 'updated-report@example.test')
    for condition in ('wet','good'):
        assert client.post('/api/courts/1/condition', json={'condition':condition}, headers=auth(user['token'])).status_code == 201
    reports = client.get('/api/courts/1').get_json()['condition_reports']
    assert len(reports) == 1 and reports[0]['condition'] == 'good'
