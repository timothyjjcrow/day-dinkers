"""Foreign scope exclusions affect discovery, never original detail or history."""
from datetime import timedelta
import json

import pytest
from sqlalchemy import inspect

from backend.app import _upgrade_schema, create_app, db
from backend.models import (
    Court, CourtAlias, CourtDirectoryExclusion, CourtReview, FavoriteCourt,
    Game, utcnow,
)
from scripts.migrate_production_schema import _upgrade_directory_exclusion


@pytest.fixture()
def client():
    app = create_app('testing')
    with app.app_context():
        db.drop_all()
        db.create_all()
        always_open = {day: {'open': '00:00', 'close': '00:00'}
                       for day in ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')}
        always_open['timezone'] = 'America/New_York'
        db.session.add_all([
            Court(id=1, name='Local Park', city='Cape Coral', state='FL',
                  address='1 Local Street', latitude=26.64, longitude=-81.98,
                  num_courts=2),
            # Imported US pin remains intact, even though evidence is foreign.
            Court(id=2, name='Heaton Tennis Club', city='Cape Coral', state='FL',
                  address='2 Import Street', latitude=26.640001, longitude=-81.980001,
                  num_courts=100, lighted=True, indoor=True, has_restrooms=True,
                  has_water=True, nets_provided=True, structured_hours=json.dumps(always_open)),
            Court(id=3, name='Restored Park', city='Cape Coral', state='FL',
                  latitude=26.645, longitude=-81.985, num_courts=4,
                  structured_hours=json.dumps(always_open)),
            Court(id=4, name='Overseas Legacy Name', city='Cape Coral', state='FL',
                  latitude=26.644, longitude=-81.984, num_courts=100),
        ])
        db.session.flush()
        for court_id in (2, 3, 4):
            db.session.add(CourtDirectoryExclusion(
                court_id=court_id, active=court_id != 3, reason_code='foreign_venue',
                reason='Primary operator confirms this venue in England.',
                source_urls='["https://example.test/official"]', reviewed_by='Private reviewer'))
        db.session.add(CourtAlias(alias_court_id=4, canonical_court_id=1,
                                 reason='Existing alias configuration.',
                                 source_urls='["https://example.test/official"]', reviewed_by='Reviewer'))
        db.session.commit()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def listing(client, query=''):
    response = client.get('/api/courts' + ('?' + query if query else ''))
    assert response.status_code == 200, response.get_json()
    return response.get_json()


@pytest.mark.parametrize('scope', ['', 'bbox=-83,25,-80,28', 'lat=26.64&lng=-81.98&radius=100'])
@pytest.mark.parametrize('sort', ['courts', 'rating', 'active', 'distance'])
def test_excluded_high_rank_venues_never_consume_counts_or_cursor_pages(client, scope, sort):
    query = f'{scope}&sort={sort}&limit=1'
    seen = []
    payload = listing(client, query)
    while True:
        assert payload['total'] == 2
        seen.extend(row['id'] for row in payload['items'])
        if not payload['has_more']:
            break
        payload = listing(client, query + '&cursor=' + payload['next_cursor'])
    assert len(seen) == len(set(seen)) == 2
    assert set(seen) == {1, 3}


@pytest.mark.parametrize('query', ['Heaton Tennis Club', 'Heaton', 'Heaton Tenis Club', 'Overseas Legacy Name'])
def test_literal_fuzzy_and_alias_search_never_restore_excluded_venues(client, query):
    assert listing(client, 'q=' + query)['total'] == 0


@pytest.mark.parametrize('filter_name', ['lighted', 'indoor', 'restrooms', 'water', 'nets'])
def test_amenity_filter_uses_only_visible_candidates(client, filter_name):
    assert listing(client, filter_name + '=1')['total'] == 0
    assert listing(client, 'q=Heaton&' + filter_name + '=1')['total'] == 0


def test_open_now_exclusion_and_database_rollback_take_effect_immediately(client):
    assert [row['id'] for row in listing(client, 'open_now=1')['items']] == [3]
    with client.application.app_context():
        db.session.get(CourtDirectoryExclusion, 2).active = False
        db.session.get(CourtDirectoryExclusion, 4).active = False
        db.session.commit()
    assert listing(client)['total'] == 3  # Existing alias still suppresses 4.
    assert listing(client, 'q=Heaton')['items'][0]['id'] == 2
    assert listing(client, 'q=Overseas Legacy Name')['items'][0]['id'] == 1
    assert 'directory_context' not in client.get('/api/courts/2').get_json()


def test_original_detail_play_favorites_reviews_and_games_keep_identity(client):
    detail = client.get('/api/courts/2').get_json()
    assert detail['id'] == 2 and detail['name'] == 'Heaton Tennis Club'
    assert detail['closed'] is False and detail['pending_submission'] is False
    assert detail['directory_context'] == {
        'listed': False,
        'message': 'This venue is outside the United States and is not listed in the US court directory.',
    }
    assert not {'reason', 'source_urls', 'reviewed_by'} & set(detail['directory_context'])
    account = client.post('/api/auth/register', json={
        'email': 'foreign-history@example.test', 'password': 'secret123',
        'display_name': 'History Player',
    }).get_json()
    headers = {'Authorization': f"Bearer {account['token']}"}
    start = (utcnow() + timedelta(days=2)).isoformat() + 'Z'
    planned = client.post('/api/courts/2/planning-times', json={
        'starts_at': [start], 'duration_minutes': 60,
    }, headers=headers)
    assert planned.status_code == 200 and planned.get_json()['court_id'] == 2
    response = client.post('/api/games', json={
        'court_id': 2, 'scheduled_at': start, 'game_type': 'casual',
        'visibility': 'open', 'max_players': 4,
    }, headers=headers)
    assert response.status_code == 201, response.get_json()
    game_id = response.get_json()['id']
    checked_in = client.post('/api/courts/2/checkin', json={
        'presence_intent': 'self_reported', 'confirm_at_court': True,
    }, headers=headers)
    assert checked_in.status_code == 200, checked_in.get_json()
    with client.application.app_context():
        user_id = account['user']['id']
        db.session.add_all([
            CourtReview(court_id=2, user_id=user_id, rating=5, comment='Original history'),
            FavoriteCourt(court_id=2, user_id=user_id),
        ])
        db.session.commit()
        assert db.session.get(Game, game_id).court_id == 2
        assert Court.query.count() == 4
        assert not db.session.get(Court, 2).closed
        assert not db.session.get(Court, 2).pending_submission
    assert client.get('/api/courts/2', headers=headers).get_json()['rating_count'] == 1
    assert client.get('/api/courts/2/play').status_code == 200
    assert client.get(f'/api/games/{game_id}', headers=headers).get_json()['court']['id'] == 2
    assert [r['id'] for r in client.get('/api/courts/favorites', headers=headers).get_json()['items']] == [2]
    assert listing(client, 'sort=rating')['total'] == 2


def test_additive_migration_is_idempotent_and_preserves_court_rows(client):
    with client.application.app_context():
        db.session.remove()
        CourtDirectoryExclusion.__table__.drop(db.engine)
        client.application.config['AUTO_CREATE_DB'] = False
        _upgrade_schema(client.application)
        assert 'court_directory_exclusion' not in inspect(db.engine).get_table_names()
        _upgrade_directory_exclusion(db.engine)
        _upgrade_directory_exclusion(db.engine)
        inspector = inspect(db.engine)
        assert inspector.get_pk_constraint('court_directory_exclusion')['constrained_columns'] == ['court_id']
        assert 'ck_court_directory_exclusion_reason' in {
            r['name'] for r in inspector.get_check_constraints('court_directory_exclusion')}
        fk = inspector.get_foreign_keys('court_directory_exclusion')[0]
        assert fk['referred_table'] == 'court' and fk['options']['ondelete'] == 'RESTRICT'
        assert Court.query.count() == 4


def test_admitted_test_fixture_uses_clear_context_and_preserves_original_detail(client):
    with client.application.app_context():
        exclusion = db.session.get(CourtDirectoryExclusion, 2)
        exclusion.reason_code = 'invalid_test_record'
        db.session.commit()
    assert listing(client, 'q=Heaton')['total'] == 0
    detail = client.get('/api/courts/2').get_json()
    assert detail['id'] == 2 and not detail['closed'] and not detail['pending_submission']
    assert 'test record' in detail['directory_context']['message']
    assert 'outside the United States' not in detail['directory_context']['message']


def test_schema_management_disabled_runtime_performs_no_exclusion_ddl(monkeypatch):
    from backend import config
    monkeypatch.setattr(config.TestingConfig, 'SCHEMA_MANAGEMENT_ENABLED', False)
    monkeypatch.setattr(config.TestingConfig, 'AUTO_CREATE_DB', False)
    app = create_app('testing')
    with app.app_context():
        assert inspect(db.engine).get_table_names() == []
