"""Direct challenges use explicit state; note glyphs are compatibility only."""

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from backend.app import _upgrade_schema, create_app, db
from backend.models import Court, Game, GameInvite, GamePlayer, utcnow


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / 'public' / 'app-v15.js').read_text()


@pytest.fixture()
def app():
    app = create_app('testing')
    with app.app_context():
        db.drop_all()
        db.create_all()
        db.session.add(Court(
            name='Challenge Test Court',
            city='Costa Mesa',
            state='CA',
            county_slug='orange-county',
            latitude=33.66,
            longitude=-117.91,
            num_courts=4,
        ))
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, slug, name):
    response = client.post('/api/auth/register', json={
        'email': f'{slug}@example.com',
        'password': 'secret123',
        'display_name': name,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def headers(person):
    return {'Authorization': f"Bearer {person['token']}"}


def test_challenge_endpoint_writes_and_serializes_explicit_semantics(client, app):
    actor = register(client, 'challenge-actor', 'Actor')
    target = register(client, 'challenge-target', 'Target')
    with app.app_context():
        court_id = Court.query.one().id

    response = client.post(
        f"/api/users/{target['user']['id']}/challenge",
        json={'court_id': court_id},
        headers=headers(actor),
    )

    assert response.status_code == 201, response.get_json()
    payload = response.get_json()
    assert payload['is_challenge'] is True
    # Copy remains human-readable; the presentation glyph is no longer data.
    assert payload['notes'] == 'Actor challenged Target!'
    with app.app_context():
        game = db.session.get(Game, payload['id'])
        assert game.is_challenge is True
        assert game.is_direct_challenge is True

    active = client.get('/api/me', headers=headers(target)).get_json()['active_game']
    assert active['id'] == payload['id']
    assert active['is_challenge'] is True
    assert active['banner_state'] == 'challenge'


def test_generic_create_cannot_turn_notes_or_client_field_into_challenge(client, app):
    actor = register(client, 'ordinary-actor', 'Ordinary Actor')
    invitee = register(client, 'ordinary-invitee', 'Ordinary Invitee')
    with app.app_context():
        court_id = Court.query.one().id

    response = client.post('/api/games', json={
        'court_id': court_id,
        'scheduled_at': (utcnow() + timedelta(hours=1)).isoformat() + 'Z',
        'game_type': 'ranked',
        'visibility': 'private',
        'max_players': 2,
        'invite_user_ids': [invitee['user']['id']],
        'notes': '⚔️ This is user-authored copy, not a challenge',
        # The generic endpoint must not trust this server-owned field either.
        'is_challenge': True,
    }, headers=headers(actor))

    assert response.status_code == 201, response.get_json()
    payload = response.get_json()
    assert payload['is_challenge'] is False
    with app.app_context():
        game = db.session.get(Game, payload['id'])
        assert game.is_challenge is False
        assert game.is_direct_challenge is False

    active = client.get(
        '/api/me', headers=headers(invitee),
    ).get_json()['active_game']
    assert active['id'] == payload['id']
    assert active['banner_state'] == 'invited'


def test_null_legacy_row_uses_narrow_note_fallback(client, app):
    actor = register(client, 'legacy-actor', 'Legacy Actor')
    target = register(client, 'legacy-target', 'Legacy Target')
    with app.app_context():
        court_id = Court.query.one().id
        game = Game(
            court_id=court_id,
            creator_id=actor['user']['id'],
            scheduled_at=utcnow(),
            game_type='ranked',
            visibility='private',
            max_players=2,
            notes='⚔️ Legacy Actor challenged Legacy Target!',
            is_challenge=False,
        )
        db.session.add(game)
        db.session.flush()
        game_id = game.id
        db.session.add_all([
            GamePlayer(game_id=game_id, user_id=actor['user']['id']),
            GameInvite(game_id=game_id, user_id=target['user']['id']),
        ])
        db.session.commit()
        # Simulate a row inserted by an old application process after the
        # nullable column was installed during a rolling upgrade.
        db.session.execute(text(
            'UPDATE game SET is_challenge = NULL WHERE id = :game_id'
        ), {'game_id': game_id})
        db.session.commit()
        db.session.expire_all()
        assert db.session.get(Game, game_id).is_direct_challenge is True

    active = client.get('/api/me', headers=headers(target)).get_json()['active_game']
    assert active['id'] == game_id
    assert active['is_challenge'] is True
    assert active['banner_state'] == 'challenge'


def test_additive_upgrade_backfills_legacy_challenges_and_is_idempotent():
    migration_app = create_app('testing')
    with migration_app.app_context():
        db.session.remove()
        db.drop_all()
        with db.engine.begin() as connection:
            connection.execute(text('''
                CREATE TABLE game (
                    id INTEGER PRIMARY KEY,
                    notes VARCHAR(500) NOT NULL DEFAULT '',
                    game_type VARCHAR(20) NOT NULL DEFAULT 'casual',
                    visibility VARCHAR(16) NOT NULL DEFAULT 'open',
                    max_players INTEGER NOT NULL DEFAULT 4,
                    status VARCHAR(32) NOT NULL DEFAULT 'upcoming'
                )
            '''))
            connection.execute(text('''
                INSERT INTO game
                    (id, notes, game_type, visibility, max_players, status)
                VALUES
                    (1, '⚔️ Legacy challenge', 'ranked', 'private', 2, 'upcoming'),
                    (2, '⚔️ Ordinary note', 'casual', 'open', 4, 'upcoming'),
                    (3, 'No marker', 'ranked', 'private', 2, 'upcoming')
            '''))

        _upgrade_schema(migration_app)
        columns = {
            column['name'] for column in inspect(db.engine).get_columns('game')
        }
        assert 'is_challenge' in columns
        rows = db.session.execute(text(
            'SELECT id, is_challenge FROM game ORDER BY id'
        )).all()
        assert [(row.id, bool(row.is_challenge)) for row in rows] == [
            (1, True), (2, False), (3, False),
        ]

        # A legacy process can still omit the field after the DDL. The next
        # idempotent upgrade pass converges that row without touching an
        # explicit False written by current code.
        db.session.execute(text('''
            INSERT INTO game
                (id, notes, game_type, visibility, max_players, status,
                 is_challenge)
            VALUES
                (4, '⚔ Legacy rolling row', 'ranked', 'private', 2,
                 'upcoming', NULL),
                (5, '⚔ Explicit ordinary row', 'ranked', 'private', 2,
                 'upcoming', 0)
        '''))
        db.session.commit()
        _upgrade_schema(migration_app)
        rows = db.session.execute(text(
            'SELECT id, is_challenge FROM game WHERE id IN (4, 5) ORDER BY id'
        )).all()
        assert [(row.id, bool(row.is_challenge)) for row in rows] == [
            (4, True), (5, False),
        ]
        db.session.remove()
        db.drop_all()


def test_frontend_prefers_explicit_challenge_state_with_legacy_fallback_only():
    assert 'function gameIsChallenge(game)' in APP_JS
    assert "typeof game.is_challenge === 'boolean'" in APP_JS
    assert 'return game.is_challenge;' in APP_JS
    assert "String(game.notes || '').startsWith('⚔')" in APP_JS
    assert APP_JS.count('const isChallenge = gameIsChallenge(game);') >= 2
    assert "String(game.notes || '').startsWith('⚔️')" not in APP_JS
