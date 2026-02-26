import os
from flask import Flask, send_from_directory, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from flask_cors import CORS
from sqlalchemy import inspect, text
from backend.config import config

db = SQLAlchemy()
socketio = SocketIO()

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), '..', 'frontend')


def _parse_allowed_origins(raw_origins):
    if not raw_origins:
        return '*'

    if isinstance(raw_origins, (list, tuple, set)):
        cleaned = [origin for origin in raw_origins if origin]
        return cleaned or '*'

    raw_text = str(raw_origins).strip()
    if not raw_text or raw_text == '*':
        return '*'

    origins = [origin.strip() for origin in raw_text.split(',') if origin.strip()]
    return origins or '*'


def _run_lightweight_migrations():
    """Apply small schema updates for local/dev databases without Alembic."""
    inspector = inspect(db.engine)
    table_names = inspector.get_table_names()
    if 'user' not in table_names:
        return

    user_columns = {col['name'] for col in inspector.get_columns('user')}
    court_columns = {col['name'] for col in inspector.get_columns('court')} if 'court' in table_names else set()
    checkin_columns = {col['name'] for col in inspector.get_columns('check_in')} if 'check_in' in table_names else set()
    match_columns = {col['name'] for col in inspector.get_columns('match')} if 'match' in table_names else set()
    message_columns = {col['name'] for col in inspector.get_columns('message')} if 'message' in table_names else set()
    with db.engine.begin() as connection:
        if 'is_admin' not in user_columns:
            connection.execute(text(
                'ALTER TABLE "user" ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0'
            ))
        if 'google_sub' not in user_columns:
            connection.execute(text(
                'ALTER TABLE "user" ADD COLUMN google_sub VARCHAR(255)'
            ))
            connection.execute(text(
                'CREATE UNIQUE INDEX IF NOT EXISTS ix_user_google_sub ON "user" (google_sub)'
            ))
        if 'failed_login_attempts' not in user_columns:
            connection.execute(text(
                'ALTER TABLE "user" ADD COLUMN failed_login_attempts INTEGER NOT NULL DEFAULT 0'
            ))
        if 'locked_until' not in user_columns:
            connection.execute(text(
                'ALTER TABLE "user" ADD COLUMN locked_until TIMESTAMP'
            ))
        if 'password_reset_token_hash' not in user_columns:
            connection.execute(text(
                'ALTER TABLE "user" ADD COLUMN password_reset_token_hash VARCHAR(128)'
            ))
        if 'password_reset_token_expires_at' not in user_columns:
            connection.execute(text(
                'ALTER TABLE "user" ADD COLUMN password_reset_token_expires_at TIMESTAMP'
            ))
        connection.execute(text(
            'CREATE INDEX IF NOT EXISTS ix_user_locked_until ON "user" (locked_until)'
        ))
        connection.execute(text(
            'CREATE INDEX IF NOT EXISTS ix_user_password_reset_token_hash ON "user" (password_reset_token_hash)'
        ))

        if 'court' in table_names:
            if 'county_slug' not in court_columns:
                connection.execute(text(
                    "ALTER TABLE court ADD COLUMN county_slug VARCHAR(80) NOT NULL DEFAULT 'humboldt'"
                ))
            connection.execute(text(
                "UPDATE court SET county_slug = 'humboldt' WHERE county_slug IS NULL OR TRIM(county_slug) = ''"
            ))
            connection.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_court_county_slug ON court (county_slug)'
            ))

        if 'check_in' in table_names and 'last_presence_ping_at' not in checkin_columns:
            connection.execute(text(
                'ALTER TABLE check_in ADD COLUMN last_presence_ping_at TIMESTAMP'
            ))
            connection.execute(text(
                'UPDATE check_in SET last_presence_ping_at = checked_in_at WHERE last_presence_ping_at IS NULL'
            ))

        if 'message' in table_names:
            if 'session_id' not in message_columns:
                connection.execute(text(
                    'ALTER TABLE message ADD COLUMN session_id INTEGER'
                ))
            connection.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_message_court_type_created '
                'ON message (court_id, msg_type, created_at)'
            ))
            connection.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_message_session_type_created '
                'ON message (session_id, msg_type, created_at)'
            ))

        # Ranked queue: ensure one active queue entry per user
        if 'ranked_queue' in table_names:
            connection.execute(text(
                'DELETE FROM ranked_queue WHERE id NOT IN ('
                '  SELECT MAX(id) FROM ranked_queue GROUP BY user_id'
                ')'
            ))
            connection.execute(text(
                'CREATE UNIQUE INDEX IF NOT EXISTS ix_ranked_queue_user '
                'ON ranked_queue (user_id)'
            ))
            connection.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_ranked_queue_court_joined_at '
                'ON ranked_queue (court_id, joined_at)'
            ))

        if 'play_session_player' in table_names:
            connection.execute(text(
                'DELETE FROM play_session_player WHERE id NOT IN ('
                '  SELECT MIN(id) FROM play_session_player GROUP BY session_id, user_id'
                ')'
            ))
            connection.execute(text(
                'CREATE UNIQUE INDEX IF NOT EXISTS ix_play_session_player_session_user '
                'ON play_session_player (session_id, user_id)'
            ))

        # Performance indexes for ranked queries
        if 'match' in table_names:
            if 'tournament_id' not in match_columns:
                connection.execute(text(
                    'ALTER TABLE "match" ADD COLUMN tournament_id INTEGER'
                ))
            if 'bracket_round' not in match_columns:
                connection.execute(text(
                    'ALTER TABLE "match" ADD COLUMN bracket_round INTEGER'
                ))
            if 'bracket_slot' not in match_columns:
                connection.execute(text(
                    'ALTER TABLE "match" ADD COLUMN bracket_slot INTEGER'
                ))
            connection.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_match_court_status '
                'ON match (court_id, status)'
            ))
            connection.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_match_tournament_round_slot '
                'ON match (tournament_id, bracket_round, bracket_slot)'
            ))
        if 'match_player' in table_names:
            connection.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_match_player_user_confirmed '
                'ON match_player (user_id, confirmed)'
            ))
        if 'ranked_lobby' in table_names:
            connection.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_ranked_lobby_court_status '
                'ON ranked_lobby (court_id, status)'
            ))
        if 'tournament' in table_names:
            connection.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_tournament_court_status_start '
                'ON tournament (court_id, status, start_time)'
            ))
        if 'tournament_participant' in table_names:
            connection.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_tournament_participant_tournament_status '
                'ON tournament_participant (tournament_id, participant_status)'
            ))
        if 'tournament_result' in table_names:
            connection.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_tournament_result_court_points '
                'ON tournament_result (court_id, points)'
            ))
            connection.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_tournament_result_user_created '
                'ON tournament_result (user_id, created_at)'
            ))


def _repair_court_county_assignments():
    """Keep court county slugs aligned with coordinate bounds."""
    from backend.models import Court
    from backend.services.california_county_bounds import (
        is_point_within_county_bounds,
        resolve_county_slug_for_point,
    )

    updated = 0
    removed = 0
    courts = Court.query.all()
    for court in courts:
        lat = getattr(court, 'latitude', None)
        lng = getattr(court, 'longitude', None)
        if lat is None or lng is None:
            continue

        if is_point_within_county_bounds(lat, lng, court.county_slug):
            continue

        resolved_slug = resolve_county_slug_for_point(lat, lng, preferred_slug='')
        if resolved_slug:
            if resolved_slug != court.county_slug:
                court.county_slug = resolved_slug
                updated += 1
        else:
            db.session.delete(court)
            removed += 1

    if updated or removed:
        db.session.commit()


def create_app(config_name='development'):
    app = Flask(__name__)
    app.config.from_object(config[config_name])

    allowed_origins = _parse_allowed_origins(app.config.get('CORS_ALLOWED_ORIGINS', '*'))
    if str(config_name).strip().lower() == 'production':
        secret_key = str(app.config.get('SECRET_KEY') or '').strip()
        if not secret_key or secret_key == 'dev-secret-key-change-in-prod':
            raise RuntimeError('SECRET_KEY must be set to a non-default value in production')
        if allowed_origins == '*':
            raise RuntimeError('CORS_ALLOWED_ORIGINS must be explicitly set in production')

    db.init_app(app)
    socketio.init_app(
        app,
        cors_allowed_origins=allowed_origins,
        async_mode=app.config.get('SOCKETIO_ASYNC_MODE', 'threading'),
    )
    CORS(app, resources={r'/api/*': {'origins': allowed_origins}})

    @app.before_request
    def _enforce_origin_for_mutating_api_requests():
        if request.method in {'GET', 'HEAD', 'OPTIONS'}:
            return None
        if not request.path.startswith('/api/'):
            return None

        origin = str(request.headers.get('Origin') or '').strip()
        if not origin:
            return None

        configured_origins = _parse_allowed_origins(
            app.config.get('CORS_ALLOWED_ORIGINS', '*')
        )
        if configured_origins != '*' and origin not in configured_origins:
            return jsonify({'error': 'Invalid request origin'}), 403

        auth_header = str(request.headers.get('Authorization') or '').strip()
        if not auth_header:
            return None

        csrf_header = request.headers.get('X-CSRF-Token')
        from backend.auth_utils import csrf_token_matches
        if not csrf_token_matches(auth_header, csrf_header):
            return jsonify({'error': 'Invalid CSRF token'}), 403
        return None

    from backend.routes.auth import auth_bp
    from backend.routes.courts import courts_bp
    from backend.routes.games import games_bp
    from backend.routes.sessions import sessions_bp
    from backend.routes.chat import chat_bp
    from backend.routes.presence import presence_bp
    from backend.routes.ranked import ranked_bp

    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(courts_bp, url_prefix='/api/courts')
    app.register_blueprint(games_bp, url_prefix='/api/games')
    app.register_blueprint(sessions_bp, url_prefix='/api/sessions')
    app.register_blueprint(chat_bp, url_prefix='/api/chat')
    app.register_blueprint(presence_bp, url_prefix='/api/presence')
    app.register_blueprint(ranked_bp, url_prefix='/api/ranked')

    @app.route('/')
    def index():
        return send_from_directory(FRONTEND_DIR, 'index.html')

    @app.route('/<path:filename>')
    def frontend_files(filename):
        return send_from_directory(FRONTEND_DIR, filename)

    with app.app_context():
        from backend import models  # noqa: F401
        db.create_all()
        _run_lightweight_migrations()
        _repair_court_county_assignments()

    return app
