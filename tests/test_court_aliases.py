"""Directory aliases preserve original IDs and all play/history contracts."""
from datetime import timedelta
import json

import pytest
from sqlalchemy import inspect, text

from backend.app import _upgrade_schema, create_app, db
from backend.models import BlockedUser, CheckIn, Court, CourtAlias, CourtReview, FavoriteCourt, Game, GamePlayer, utcnow


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
            Court(id=1, name='The Courts Cape Coral', city='Cape Coral', state='FL',
                  address='420 SW 2nd Avenue', latitude=26.64, longitude=-81.98,
                  num_courts=32, lighted=False, fee_type='fee',
                  structured_hours=json.dumps(always_open)),
            # Deliberately different facts: search and filtering must use court 1.
            Court(id=2, name='Lake Kennedy Racquet Center', city='Old Park', state='FL',
                  address='400 Santa Barbara Blvd', latitude=27.0, longitude=-82.0,
                  num_courts=32, lighted=True, indoor=True, fee_type='free'),
            Court(id=3, name='Nearby Courts', city='Cape Coral', state='FL',
                  latitude=26.645, longitude=-81.985, num_courts=4,
                  lighted=True, fee_type='free'),
            Court(id=4, name='Distant Courts', city='Miami', state='FL',
                  latitude=25.66, longitude=-80.35, num_courts=2),
        ])
        db.session.flush()
        db.session.add(CourtAlias(alias_court_id=2, canonical_court_id=1,
                                 reason='Official operator confirms the same venue.',
                                 source_urls='["https://example.test/venue"]', reviewed_by='Reviewer'))
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
def test_alias_is_hidden_before_count_sort_and_pagination(client, scope, sort):
    query = f'{scope}&sort={sort}&limit=1'
    expected = {1, 3, 4} if 'radius' not in scope else {1, 3}
    seen = []
    payload = listing(client, query)
    while True:
        assert payload['total'] == len(expected)
        seen.extend(row['id'] for row in payload['items'])
        if not payload['has_more']:
            break
        payload = listing(client, query + '&cursor=' + payload['next_cursor'])
    assert len(seen) == len(set(seen))
    assert set(seen) == expected
    # Canonical court count is unchanged; duplicate courts aren't summed.
    assert listing(client, 'q=The Courts Cape Coral')['items'][0]['num_courts'] == 32


@pytest.mark.parametrize('query', ['Lake Kennedy', 'lake kennedy racquet center', 'Kenedy', 'Lake Kenedy Racquet Center'])
def test_literal_and_fuzzy_alias_names_find_canonical_once(client, query):
    payload = listing(client, 'q=' + query)
    assert payload['total'] == 1
    assert [row['id'] for row in payload['items']] == [1]
    assert payload['items'][0]['name'] == 'The Courts Cape Coral'


def test_alias_match_uses_canonical_geography_amenities_and_hours(client):
    assert listing(client, 'q=Lake Kennedy&lighted=1')['total'] == 0
    assert listing(client, 'q=Lake Kennedy&indoor=1')['total'] == 0
    assert listing(client, 'q=Lake Kennedy&bbox=-82.1,26.9,-81.9,27.1')['total'] == 0
    payload = listing(client, 'q=Lake Kennedy&bbox=-82,26.6,-81.9,26.7&open_now=1')
    assert [row['id'] for row in payload['items']] == [1]
    assert listing(client, 'lighted=1&limit=1')['total'] == 1


def test_multiple_aliases_deduplicate_and_database_toggle_is_immediate(client):
    with client.application.app_context():
        db.session.add(CourtAlias(alias_court_id=3, canonical_court_id=1,
                                 reason='Reviewed second name of the same venue.',
                                 source_urls='["https://example.test/venue"]', reviewed_by='Reviewer'))
        db.session.commit()
    assert listing(client, 'q=Cape Coral')['total'] == 1
    assert listing(client)['total'] == 2
    with client.application.app_context():
        db.session.get(CourtAlias, 2).active = False
        db.session.commit()
    assert listing(client)['total'] == 3
    assert listing(client, 'q=Lake Kennedy')['items'][0]['id'] == 2


@pytest.mark.parametrize('query', ['%', '_', '%25', '%5F'])
def test_wildcards_on_alias_search_do_not_broaden_directory(client, query):
    assert listing(client, 'q=' + query)['total'] == 0


def test_original_deep_links_and_new_play_keep_original_id(client):
    original = client.get('/api/courts/2')
    assert original.status_code == 200
    assert original.get_json()['id'] == 2
    assert original.get_json()['name'] == 'Lake Kennedy Racquet Center'
    account = client.post('/api/auth/register', json={
        'email': 'alias-player@example.test', 'password': 'secret123',
        'display_name': 'Alias Player',
    }).get_json()
    headers = {'Authorization': f"Bearer {account['token']}"}
    start = (utcnow() + timedelta(days=2)).isoformat() + 'Z'
    planning = client.post('/api/courts/2/planning-times', json={
        'starts_at': [start], 'duration_minutes': 60,
    }, headers=headers)
    assert planning.status_code == 200
    assert planning.get_json()['court_id'] == 2
    created = client.post('/api/games', json={
        'court_id': 2, 'scheduled_at': start, 'game_type': 'casual',
        'visibility': 'open', 'max_players': 4,
    }, headers=headers)
    assert created.status_code == 201, created.get_json()
    game_id = created.get_json()['id']
    checked_in = client.post('/api/courts/2/checkin', json={
        'presence_intent': 'self_reported', 'confirm_at_court': True,
    }, headers=headers)
    assert checked_in.status_code == 200, checked_in.get_json()
    assert client.get('/api/courts/2/play').status_code == 200
    assert client.get(f'/api/games/{game_id}', headers=headers).get_json()['court']['id'] == 2
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
    # History/favorites/reviews remain under original IDs, even if created later.
    assert client.get('/api/courts/2', headers=headers).get_json()['rating_count'] == 1
    canonical = client.get('/api/courts/1', headers=headers).get_json()
    assert canonical['rating_count'] == 1
    assert canonical['players_here_count'] == 1
    assert canonical['id'] == 1
    assert [row['id'] for row in canonical['games']] == [game_id]
    player_items = [row for row in client.get('/api/courts/1/play').get_json()['items'] if row['source'] == 'player']
    assert [row['game']['id'] for row in player_items] == [game_id]
    saved = client.get('/api/courts/favorites', headers=headers).get_json()['items']
    assert [row['id'] for row in saved] == [2]
    mapped = listing(client, 'q=Lake Kennedy')['items'][0]
    assert mapped['rating_count'] == 1
    assert mapped['players_here'] == 1
    assert mapped['upcoming_games'] == 1


def account(client, label):
    response = client.post('/api/auth/register', json={
        'email': f'{label}@example.test', 'password': 'secret123', 'display_name': label,
    })
    assert response.status_code == 201
    result = response.get_json()
    return result['user']['id'], {'Authorization': f"Bearer {result['token']}"}


def test_family_review_latest_per_player_agrees_with_sort_reader_and_own_actions(client):
    owner, headers = account(client, 'review-owner')
    other, other_headers = account(client, 'review-other')
    with client.application.app_context():
        timestamp = utcnow()
        rows = [
            CourtReview(court_id=1, user_id=owner, rating=1, comment='Older canonical', updated_at=timestamp),
            CourtReview(court_id=2, user_id=owner, rating=5, comment='Latest alias', updated_at=timestamp),
            CourtReview(court_id=2, user_id=other, rating=3, comment='Other player'),
            CourtReview(court_id=3, user_id=other, rating=3, comment='Other venue'),
        ]
        db.session.add_all(rows)
        db.session.commit()
        own_alias_id, other_alias_id = rows[1].id, rows[2].id
    # Tied timestamps choose the greater ID. One player counts only once.
    first = listing(client, 'sort=rating&limit=1')
    assert first['items'][0]['id'] == 1
    assert first['items'][0]['rating_avg'] == 4.0
    assert first['items'][0]['rating_count'] == 2
    canonical = client.get('/api/courts/1', headers=headers).get_json()
    assert canonical['rating_count'] == 2 and canonical['rating_avg'] == 4.0
    assert canonical['my_review']['id'] == own_alias_id
    reader = client.get('/api/courts/1/reviews', headers=headers).get_json()
    assert {row['comment'] for row in reader['items']} == {'Latest alias', 'Other player'}
    assert reader['my_review']['id'] == own_alias_id
    assert reader['rating_count'] == 2
    assert client.get('/api/courts/2/reviews', headers=headers).get_json()['rating_count'] == 2
    assert client.get('/api/courts/1/reviews').get_json()['my_review'] is None
    assert client.delete(f'/api/courts/1/reviews/{other_alias_id}', headers=headers).status_code == 403
    edited = client.post('/api/courts/1/reviews', json={'rating': 4, 'comment': 'Edited canonical view'}, headers=headers)
    assert edited.status_code == 201
    assert edited.get_json()['review']['id'] == own_alias_id
    with client.application.app_context():
        assert db.session.get(CourtReview, own_alias_id).court_id == 2
        assert CourtReview.query.count() == 4
    deleted = client.delete(f'/api/courts/1/reviews/{own_alias_id}', headers=headers)
    assert deleted.status_code == 200
    assert deleted.get_json()['rating_count'] == 1
    assert client.get('/api/courts/1/reviews', headers=headers).get_json()['my_review'] is None
    with client.application.app_context():
        assert CourtReview.query.filter_by(user_id=owner).count() == 0
        assert CourtReview.query.filter_by(user_id=other).count() == 2
    # A direct alias delete stays exact-ID and cannot remove canonical feedback.
    canonical_own = client.post('/api/courts/1/reviews', json={'rating': 2}, headers=headers).get_json()['review']
    alias_own = client.post('/api/courts/2/reviews', json={'rating': 5}, headers=headers).get_json()['review']
    assert client.delete(f"/api/courts/2/reviews/{canonical_own['id']}", headers=headers).status_code == 404
    assert client.delete(f"/api/courts/2/reviews/{alias_own['id']}", headers=headers).status_code == 200
    assert client.get('/api/courts/1/reviews', headers=headers).get_json()['my_review']['id'] == canonical_own['id']


def test_alias_activity_drives_canonical_sort_detail_play_and_existing_privacy(client):
    owner, owner_headers = account(client, 'active-owner')
    viewer, viewer_headers = account(client, 'active-viewer')
    other, _ = account(client, 'active-other')
    with client.application.app_context():
        now = utcnow()
        db.session.add_all([
            CheckIn(court_id=2, user_id=owner, checked_in_at=now,
                    last_presence_ping_at=now, looking_for_game=True),
            CheckIn(court_id=1, user_id=other, checked_in_at=now,
                    last_presence_ping_at=now, location_verified_at=now),
        ])
        open_game = Game(court_id=2, creator_id=owner, scheduled_at=now + timedelta(minutes=30),
                         max_players=4, visibility='open')
        private_game = Game(court_id=2, creator_id=owner, scheduled_at=now + timedelta(minutes=45),
                            max_players=4, visibility='private')
        db.session.add_all([open_game, private_game])
        db.session.flush()
        db.session.add_all([GamePlayer(game_id=open_game.id, user_id=owner),
                            GamePlayer(game_id=private_game.id, user_id=owner)])
        db.session.commit()
        open_id, private_id = open_game.id, private_game.id
    payload = listing(client, 'sort=active&limit=1')
    mapped = payload['items'][0]
    assert mapped['id'] == 1
    assert mapped['players_here'] == 2
    assert mapped['presence_summary']['location_confirmed'] == 1
    assert mapped['presence_summary']['self_reported'] == 1
    assert mapped['active_games'] == mapped['upcoming_games'] == 1
    canonical = client.get('/api/courts/1').get_json()
    assert canonical['players_here_count'] == 2
    assert canonical['players_here'] == []
    assert [row['id'] for row in canonical['now_games']] == [open_id]
    assert client.get('/api/courts/2').get_json()['players_here_count'] == 1
    play = client.get('/api/courts/1/play').get_json()
    assert [row['game']['id'] for row in play['items'] if row['source'] == 'player'] == [open_id]
    owner_play = client.get('/api/courts/1/play', headers=owner_headers).get_json()
    assert {row['game']['id'] for row in owner_play['items'] if row['source'] == 'player'} == {open_id, private_id}
    with client.application.app_context():
        db.session.add(BlockedUser(blocker_id=viewer, blocked_id=owner))
        db.session.commit()
    blocked = client.get('/api/courts?q=Lake Kennedy', headers=viewer_headers).get_json()['items'][0]
    assert blocked['players_here'] == 1
    assert blocked['active_games'] == blocked['upcoming_games'] == 0
    assert client.get('/api/courts/1', headers=viewer_headers).get_json()['games'] == []
    assert not [row for row in client.get('/api/courts/1/play', headers=viewer_headers).get_json()['items'] if row['source'] == 'player']
    with client.application.app_context():
        assert db.session.get(Game, open_id).court_id == 2
        assert db.session.get(CheckIn, 1).court_id == 2
        # Disabling the mapping restores separate discovery and summaries immediately.
        db.session.get(CourtAlias, 2).active = False
        db.session.commit()
    separated = {row['id']: row for row in listing(client)['items']}
    assert separated[1]['players_here'] == separated[2]['players_here'] == 1
    assert separated[1]['upcoming_games'] == 0
    assert separated[2]['upcoming_games'] == 1


def test_family_presence_counts_a_player_once_with_legacy_duplicate_visits(client):
    user_id, _ = account(client, 'legacy-presence')
    with client.application.app_context():
        # Reproduce legacy data defensively; current production's unique index
        # already prevents new duplicate active check-ins for a player.
        db.session.execute(text('DROP INDEX uq_check_in_active_user'))
        now = utcnow()
        db.session.add_all([
            CheckIn(court_id=1, user_id=user_id, checked_in_at=now - timedelta(minutes=10),
                    last_presence_ping_at=now - timedelta(minutes=1)),
            CheckIn(court_id=2, user_id=user_id, checked_in_at=now - timedelta(minutes=5),
                    last_presence_ping_at=now, location_verified_at=now),
        ])
        db.session.commit()
    mapped = listing(client, 'q=Lake Kennedy')['items'][0]
    assert mapped['players_here'] == 1
    assert mapped['presence_summary']['location_confirmed'] == 1
    assert mapped['presence_summary']['self_reported'] == 0
    canonical = client.get('/api/courts/1').get_json()
    assert canonical['players_here_count'] == 1
    assert canonical['presence_summary']['location_confirmed'] == 1
    assert client.get('/api/courts/2').get_json()['players_here_count'] == 1


def test_alias_family_preserves_production_time_poll_exclusion(client):
    user_id, _ = account(client, 'alias-time-poll')
    with client.application.app_context():
        start = utcnow() + timedelta(minutes=30)
        poll = Game(court_id=2, creator_id=user_id, scheduled_at=start,
                    visibility='open', max_players=4,
                    time_options=json.dumps([start.isoformat() + 'Z']))
        db.session.add(poll)
        db.session.flush()
        db.session.add(GamePlayer(game_id=poll.id, user_id=user_id))
        db.session.commit()
    mapped = listing(client, 'q=Lake Kennedy')['items'][0]
    assert mapped['active_games'] == mapped['upcoming_games'] == 0
    for court_id in (1, 2):
        detail = client.get(f'/api/courts/{court_id}').get_json()
        assert detail['games'] == detail['now_games'] == []
        play = client.get(f'/api/courts/{court_id}/play').get_json()
        assert not [row for row in play['items'] if row['source'] == 'player']


def test_additive_migration_creates_alias_table_without_broad_create_all(client):
    with client.application.app_context():
        db.session.remove()
        CourtAlias.__table__.drop(db.engine)
        client.application.config['AUTO_CREATE_DB'] = False
        _upgrade_schema(client.application)
        _upgrade_schema(client.application)
        inspector = inspect(db.engine)
        assert 'court_alias' in inspector.get_table_names()
        assert inspector.get_pk_constraint('court_alias')['constrained_columns'] == ['alias_court_id']
        assert {'ck_court_alias_distinct'} <= {row['name'] for row in inspector.get_check_constraints('court_alias')}
        assert {'ix_court_alias_canonical_court_id'} <= {row['name'] for row in inspector.get_indexes('court_alias')}
        assert len(inspector.get_foreign_keys('court_alias')) == 2
        assert Court.query.count() == 4


def test_runtime_with_schema_management_disabled_performs_no_alias_ddl(monkeypatch):
    from backend import config
    monkeypatch.setattr(config.TestingConfig, 'SCHEMA_MANAGEMENT_ENABLED', False)
    monkeypatch.setattr(config.TestingConfig, 'AUTO_CREATE_DB', False)
    app = create_app('testing')
    with app.app_context():
        assert inspect(db.engine).get_table_names() == []
