import json

import pytest

from backend.app import create_app, db
from backend.models import Court


@pytest.fixture()
def client():
    app = create_app('testing')
    with app.app_context():
        db.drop_all()
        db.create_all()
        court = Court(
            name='Schedule Court', city='Irvine', state='CA',
            latitude=33.68, longitude=-117.82,
            open_play_schedule='Weekend mornings; times may change',
            open_play_schedule_rows=json.dumps([{
                'weekday': 'sat', 'start': '08:00', 'end': '10:00',
                'level': 'All levels', 'cost': 'Free', 'notes': 'Paddle rotation',
            }]),
        )
        db.session.add(court)
        db.session.commit()
        court_id = court.id
        yield app.test_client(), court_id
        db.session.remove()
        db.drop_all()


def _register(client, suffix):
    response = client.post('/api/auth/register', json={
        'email': f'schedule-{suffix}@example.com',
        'password': 'secret123',
        'display_name': f'Schedule Player {suffix}',
    })
    assert response.status_code == 201, response.get_json()
    return {'Authorization': f"Bearer {response.get_json()['token']}"}


def test_normalized_open_play_rows_are_serialized_in_list_and_detail(client):
    browser, court_id = client
    listing = browser.get('/api/courts?lat=33.68&lng=-117.82&radius=5')
    assert listing.status_code == 200
    row = listing.get_json()['items'][0]['open_play_schedule_rows'][0]
    assert row == {
        'weekday': 'sat', 'start': '08:00', 'end': '10:00',
        'level': 'All levels', 'cost': 'Free', 'notes': 'Paddle rotation',
    }

    detail = browser.get(f'/api/courts/{court_id}')
    assert detail.status_code == 200
    assert detail.get_json()['open_play_schedule_rows'] == [row]
    assert detail.get_json()['open_play_schedule'] == 'Weekend mornings; times may change'


def test_two_player_consensus_applies_canonical_rows_and_keeps_legacy_fallback(client):
    browser, court_id = client
    first = _register(browser, 'one')
    second = _register(browser, 'two')
    proposal = {
        'open_play_schedule': 'Tuesday and Thursday evenings',
        'open_play_schedule_rows': [
            {
                'weekday': 'Tuesday', 'start': '18:00', 'end': '20:00',
                'level': '3.0–3.5', 'cost': '$5 drop-in', 'notes': 'Four on, four off',
            },
            {
                'weekday': 'thu', 'start': '18:00', 'end': '20:00',
                'level': '3.0–3.5', 'cost': '$5 drop-in', 'notes': '',
            },
        ],
    }
    pending = browser.post(
        f'/api/courts/{court_id}/suggest', json=proposal, headers=first,
    )
    assert pending.status_code == 201
    assert pending.get_json()['applied_fields'] == []

    applied = browser.post(
        f'/api/courts/{court_id}/suggest', json=proposal, headers=second,
    )
    assert applied.status_code == 201
    assert applied.get_json()['applied_fields'] == [
        'open_play_schedule', 'open_play_schedule_rows',
    ]
    court = applied.get_json()['court']
    assert court['open_play_schedule'] == proposal['open_play_schedule']
    assert [row['weekday'] for row in court['open_play_schedule_rows']] == ['tue', 'thu']
    assert court['open_play_schedule_rows'][0]['start'] == '18:00'
    assert court['open_play_schedule_rows'][0]['end'] == '20:00'


def test_pending_corrections_can_be_read_confirmed_or_marked_not_right(client):
    browser, court_id = client
    proposer = _register(browser, 'proposal-owner')
    reviewer = _register(browser, 'proposal-reviewer')
    confirmer = _register(browser, 'proposal-confirmer')
    proposal = {'fees': '$5 drop-in'}

    submitted = browser.post(
        f'/api/courts/{court_id}/suggest', json=proposal, headers=proposer,
    )
    assert submitted.status_code == 201

    queue = browser.get(
        f'/api/courts/{court_id}/suggestions', headers=reviewer,
    )
    assert queue.status_code == 200
    assert queue.get_json()['items'] == [{
        'field': 'fees',
        'value': '$5 drop-in',
        'confirmations': 1,
        'rejections': 0,
        'confirmed_by_me': False,
        'rejected_by_me': False,
        'needed': 1,
    }]

    rejected = browser.post(
        f'/api/courts/{court_id}/suggestions/decision',
        json={**proposal, 'field': 'fees', 'value': proposal['fees'], 'decision': 'reject'},
        headers=reviewer,
    )
    assert rejected.status_code == 200
    assert rejected.get_json()['items'][0]['rejections'] == 1
    assert rejected.get_json()['items'][0]['rejected_by_me'] is True

    confirmed = browser.post(
        f'/api/courts/{court_id}/suggestions/decision',
        json={'field': 'fees', 'value': proposal['fees'], 'decision': 'confirm'},
        headers=confirmer,
    )
    assert confirmed.status_code == 200
    assert confirmed.get_json()['applied_fields'] == ['fees']
    assert confirmed.get_json()['court']['fees'] == '$5 drop-in'
    assert confirmed.get_json()['items'] == []


@pytest.mark.parametrize('rows', [
    [{'weekday': 'funday', 'start': '08:00', 'end': '10:00'}],
    [{'weekday': 'mon', 'start': '8am', 'end': '10:00'}],
    [{'weekday': 'mon', 'start': '08:00', 'end': '08:00'}],
    'Monday mornings',
])
def test_open_play_suggestions_reject_non_time_bounded_rows(client, rows):
    browser, court_id = client
    headers = _register(browser, str(abs(hash(repr(rows)))))
    response = browser.post(
        f'/api/courts/{court_id}/suggest',
        json={'open_play_schedule_rows': rows},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.get_json() == {
        'error': 'invalid_field', 'field': 'open_play_schedule_rows',
    }
