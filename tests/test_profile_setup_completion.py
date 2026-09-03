"""Profile setup is only complete when its four useful fields are durable."""

import pytest

from backend.app import create_app, db
from backend.models import Court


@pytest.fixture()
def client():
    app = create_app('testing')
    with app.app_context():
        db.create_all()
        db.session.add(Court(
            name='Setup Court', city='Irvine', state='CA',
            latitude=33.67, longitude=-117.82, num_courts=4,
        ))
        db.session.commit()
        with app.test_client() as test_client:
            yield test_client
        db.session.remove()
        db.drop_all()


def test_completion_marker_rejects_skips_then_accepts_all_four_profile_fields(client):
    registered = client.post('/api/auth/register', json={
        'email': 'setup@example.com',
        'password': 'secret123',
        'display_name': 'Setup Player',
    })
    assert registered.status_code == 201
    payload = registered.get_json()
    headers = {'Authorization': f"Bearer {payload['token']}"}
    assert payload['user']['onboarding_complete'] is False

    incomplete = client.patch(
        '/api/me', json={'onboarding_complete': True}, headers=headers,
    )
    assert incomplete.status_code == 400
    assert incomplete.get_json() == {
        'error': 'profile_setup_incomplete',
        'missing': ['skill_rating', 'availability', 'avatar', 'home_court'],
    }
    assert client.get('/api/me', headers=headers).get_json()['user']['onboarding_complete'] is False

    court_id = client.get('/api/courts?lat=33.67&lng=-117.82', headers=headers).get_json()['items'][0]['id']
    completed = client.patch('/api/me', json={
        'skill_rating': 3.5,
        'availability': ['tue-eve', 'sat-am'],
        'avatar_url': 'https://images.example/setup-player.jpg',
        'home_court_id': court_id,
        'onboarding_complete': True,
    }, headers=headers)
    assert completed.status_code == 200, completed.get_json()
    assert completed.get_json()['user']['onboarding_complete'] is True

    # Completion is monotonic and safe to retry.
    retried = client.patch('/api/me', json={'onboarding_complete': True}, headers=headers)
    assert retried.status_code == 200
    assert retried.get_json()['user']['onboarding_complete'] is True
