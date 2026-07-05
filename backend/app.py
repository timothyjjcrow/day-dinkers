"""Flask application bootstrap."""
import os
import threading
import time

from flask import Flask, jsonify, request, send_from_directory
from flask_sqlalchemy import SQLAlchemy

from backend.config import get_config

db = SQLAlchemy(session_options={'expire_on_commit': False})

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(PROJECT_ROOT, 'frontend')
BUNDLED_COURTS_FILE = os.path.join(PROJECT_ROOT, 'data', 'courts.json.gz')


_seed_thread_started = False


def _seed_courts_background(app):
    """Import the bundled court data on first boot (runs in a thread so deploys
    don't time out while ~18k rows insert)."""
    with app.app_context():
        try:
            from backend.models import Court
            from backend.seed import import_courts_file
            if Court.query.count() > 0:
                return
            count = import_courts_file(BUNDLED_COURTS_FILE)
            app.logger.info('Auto-seeded %s courts from bundled data', count)
        except Exception:
            app.logger.exception('Court auto-seed failed')


def _maybe_auto_seed(app):
    global _seed_thread_started
    if _seed_thread_started or not app.config.get('AUTO_SEED_COURTS'):
        return
    if not os.path.exists(BUNDLED_COURTS_FILE):
        app.logger.warning('AUTO_SEED_COURTS set but %s is missing', BUNDLED_COURTS_FILE)
        return
    from backend.models import Court
    try:
        if Court.query.count() > 0:
            return
    except Exception:
        app.logger.exception('Could not check court count for auto-seed')
        return
    _seed_thread_started = True
    threading.Thread(target=_seed_courts_background, args=(app,), daemon=True).start()


def _ensure_pg_schema(app):
    """On Postgres the app lives in its own schema (search_path is set via
    connect_args), fully isolated from legacy tables in `public`."""
    if db.engine.dialect.name != 'postgresql':
        return
    from sqlalchemy import text

    from backend.config import PG_SCHEMA
    try:
        with db.engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{PG_SCHEMA}"'))
    except Exception:
        app.logger.exception('Could not ensure schema %s exists', PG_SCHEMA)


def _migrate_legacy_schema(app):
    """If the database holds the pre-rebuild schema (user table without the new
    columns), rename every old table aside so create_all can build fresh ones.
    Old data is preserved under *_legacy_<timestamp> rather than dropped."""
    from sqlalchemy import inspect as sa_inspect, text
    try:
        inspector = sa_inspect(db.engine)
        tables = inspector.get_table_names()
        if 'user' not in tables:
            return
        columns = {c['name'] for c in inspector.get_columns('user')}
        if {'password_hash', 'display_name', 'rating'} <= columns:
            return
        suffix = time.strftime('legacy_%Y%m%d%H%M%S')
        app.logger.warning(
            'Incompatible legacy schema detected — renaming %d tables to *_%s',
            len(tables), suffix,
        )
        with db.engine.begin() as conn:
            for table in tables:
                conn.execute(text(f'ALTER TABLE "{table}" RENAME TO "{table}_{suffix}"'))
    except Exception:
        app.logger.exception('Legacy schema migration failed')


def _clear_conflicting_legacy_indexes(app):
    """Renaming a table does not rename its indexes (Postgres and SQLite), so
    indexes belonging to *_legacy_* tables can still shadow names that
    create_all needs. Move those aside too."""
    from sqlalchemy import inspect as sa_inspect, text
    try:
        inspector = sa_inspect(db.engine)
        existing_tables = inspector.get_table_names()
        model_tables = set(db.metadata.tables.keys())
        wanted = {
            idx.name
            for table in db.metadata.tables.values()
            for idx in table.indexes
        }
        conflicts = []
        for table in existing_tables:
            if table in model_tables:
                continue
            for idx in inspector.get_indexes(table):
                name = idx.get('name')
                if name in wanted:
                    conflicts.append(name)
        if not conflicts:
            return
        suffix = time.strftime('legacy_%Y%m%d%H%M%S')
        app.logger.warning('Moving %d legacy indexes out of the way', len(conflicts))
        dialect = db.engine.dialect.name
        with db.engine.begin() as conn:
            for name in conflicts:
                if dialect == 'postgresql':
                    conn.execute(text(f'ALTER INDEX "{name}" RENAME TO "{name}_{suffix}"'))
                else:
                    conn.execute(text(f'DROP INDEX IF EXISTS "{name}"'))
    except Exception:
        app.logger.exception('Legacy index cleanup failed')


def _upgrade_schema(app):
    """Tiny additive migrations for existing databases (create_all only builds
    brand-new tables, it never alters existing ones)."""
    from sqlalchemy import inspect as sa_inspect, text
    try:
        inspector = sa_inspect(db.engine)
        tables = inspector.get_table_names()
        is_postgres = db.engine.dialect.name == 'postgresql'
        statements = []

        if 'message' in tables:
            columns = {c['name'] for c in inspector.get_columns('message')}
            if 'court_id' not in columns:
                statements.append('ALTER TABLE message ADD COLUMN court_id INTEGER')
                if is_postgres:
                    statements.append('ALTER TABLE message ALTER COLUMN recipient_id DROP NOT NULL')
            if 'game_id' not in columns:
                statements.append('ALTER TABLE message ADD COLUMN game_id INTEGER')
            if 'tournament_id' not in columns:
                statements.append('ALTER TABLE message ADD COLUMN tournament_id INTEGER')
            if 'club_id' not in columns:
                statements.append('ALTER TABLE message ADD COLUMN club_id INTEGER')
            if 'league_id' not in columns:
                statements.append('ALTER TABLE message ADD COLUMN league_id INTEGER')
            if 'image_data' not in columns:
                statements.append('ALTER TABLE message ADD COLUMN image_data TEXT')

        if 'user' in tables:
            user_cols = {c['name'] for c in inspector.get_columns('user')}
            for col, ddl in (
                ('last_lat', 'ALTER TABLE "user" ADD COLUMN last_lat DOUBLE PRECISION'),
                ('last_lng', 'ALTER TABLE "user" ADD COLUMN last_lng DOUBLE PRECISION'),
                ('last_location_at', 'ALTER TABLE "user" ADD COLUMN last_location_at TIMESTAMP'),
                ('home_lat', 'ALTER TABLE "user" ADD COLUMN home_lat DOUBLE PRECISION'),
                ('home_lng', 'ALTER TABLE "user" ADD COLUMN home_lng DOUBLE PRECISION'),
                ('home_area', 'ALTER TABLE "user" ADD COLUMN home_area VARCHAR(120)'),
                ('avatar_url', "ALTER TABLE \"user\" ADD COLUMN avatar_url VARCHAR(500) NOT NULL DEFAULT ''"),
                ('deleted_at', 'ALTER TABLE "user" ADD COLUMN deleted_at TIMESTAMP'),
                ('availability', 'ALTER TABLE "user" ADD COLUMN availability TEXT NOT NULL DEFAULT \'[]\''),
                ('last_recap_week', 'ALTER TABLE "user" ADD COLUMN last_recap_week VARCHAR(10) NOT NULL DEFAULT \'\''),
                ('muted_notifications', 'ALTER TABLE "user" ADD COLUMN muted_notifications TEXT NOT NULL DEFAULT \'[]\''),
                ('notified_badges', 'ALTER TABLE "user" ADD COLUMN notified_badges TEXT NOT NULL DEFAULT \'[]\''),
                ('calendar_token', 'ALTER TABLE "user" ADD COLUMN calendar_token VARCHAR(64)'),
                ('best_rating', 'ALTER TABLE "user" ADD COLUMN best_rating INTEGER NOT NULL DEFAULT 1200'),
            ):
                if col not in user_cols:
                    # SQLite uses FLOAT/DATETIME; Postgres accepts these too.
                    statements.append(ddl if is_postgres else ddl
                                      .replace('DOUBLE PRECISION', 'FLOAT')
                                      .replace('TIMESTAMP', 'DATETIME'))

        if 'game' in tables:
            game_cols = {c['name'] for c in inspector.get_columns('game')}
            if is_postgres:
                status_col = next(
                    (c for c in inspector.get_columns('game') if c['name'] == 'status'), None,
                )
                if status_col is not None and getattr(status_col['type'], 'length', 32) < 32:
                    statements.append('ALTER TABLE game ALTER COLUMN status TYPE VARCHAR(32)')
            if 'visibility' not in game_cols:
                statements.append(
                    "ALTER TABLE game ADD COLUMN visibility VARCHAR(16) NOT NULL DEFAULT 'open'"
                )
            if 'recurrence' not in game_cols:
                statements.append(
                    "ALTER TABLE game ADD COLUMN recurrence VARCHAR(16) NOT NULL DEFAULT 'none'"
                )
            if 'club_id' not in game_cols:
                statements.append('ALTER TABLE game ADD COLUMN club_id INTEGER')
            if 'preferred_level' not in game_cols:
                statements.append(
                    "ALTER TABLE game ADD COLUMN preferred_level VARCHAR(16) NOT NULL DEFAULT 'any'"
                )

        if 'court' in tables:
            court_cols = {c['name'] for c in inspector.get_columns('court')}
            if 'photo_data' not in court_cols:
                statements.append('ALTER TABLE court ADD COLUMN photo_data TEXT')
            if 'hours' not in court_cols:
                statements.append(
                    "ALTER TABLE court ADD COLUMN hours VARCHAR(255) NOT NULL DEFAULT ''"
                )
            if 'closed' not in court_cols:
                statements.append(
                    'ALTER TABLE court ADD COLUMN closed BOOLEAN NOT NULL DEFAULT '
                    + ('FALSE' if is_postgres else '0')
                )

        if 'court_photo' in tables:
            cp_cols = {c['name'] for c in inspector.get_columns('court_photo')}
            if 'caption' not in cp_cols:
                statements.append(
                    "ALTER TABLE court_photo ADD COLUMN caption VARCHAR(140) NOT NULL DEFAULT ''"
                )

        if 'tournament' in tables:
            t_cols = {c['name'] for c in inspector.get_columns('tournament')}
            for col in ('reminded_at', 'day_reminded_at'):
                if col not in t_cols:
                    statements.append(
                        f'ALTER TABLE tournament ADD COLUMN {col} '
                        + ('TIMESTAMP' if is_postgres else 'DATETIME')
                    )
            if 'ranked' not in t_cols:
                statements.append(
                    'ALTER TABLE tournament ADD COLUMN ranked BOOLEAN NOT NULL DEFAULT '
                    + ('FALSE' if is_postgres else '0')
                )
            if 'club_id' not in t_cols:
                statements.append('ALTER TABLE tournament ADD COLUMN club_id INTEGER')

        if 'tournament_entry' in tables:
            te_cols = {c['name'] for c in inspector.get_columns('tournament_entry')}
            if 'checked_in_at' not in te_cols:
                statements.append(
                    'ALTER TABLE tournament_entry ADD COLUMN checked_in_at '
                    + ('TIMESTAMP' if is_postgres else 'DATETIME')
                )

        if 'notification' in tables:
            notif_cols = {c['name'] for c in inspector.get_columns('notification')}
            if 'related_tournament_id' not in notif_cols:
                statements.append(
                    'ALTER TABLE notification ADD COLUMN related_tournament_id INTEGER'
                )
            if 'related_club_id' not in notif_cols:
                statements.append(
                    'ALTER TABLE notification ADD COLUMN related_club_id INTEGER'
                )
            if 'related_league_id' not in notif_cols:
                statements.append(
                    'ALTER TABLE notification ADD COLUMN related_league_id INTEGER'
                )

        if 'league' in tables:
            league_cols = {c['name'] for c in inspector.get_columns('league')}
            if 'round_started_at' not in league_cols:
                statements.append(
                    'ALTER TABLE league ADD COLUMN round_started_at '
                    + ('TIMESTAMP' if is_postgres else 'DATETIME')
                )
            if 'champion_user_id' not in league_cols:
                statements.append('ALTER TABLE league ADD COLUMN champion_user_id INTEGER')
            if 'club_id' not in league_cols:
                statements.append('ALTER TABLE league ADD COLUMN club_id INTEGER')

        if 'league_member' in tables:
            lm_cols = {c['name'] for c in inspector.get_columns('league_member')}
            if 'reminded_round' not in lm_cols:
                statements.append(
                    'ALTER TABLE league_member ADD COLUMN reminded_round INTEGER NOT NULL DEFAULT 0'
                )

        if 'club' in tables:
            club_cols = {c['name'] for c in inspector.get_columns('club')}
            if 'last_digest_at' not in club_cols:
                statements.append(
                    'ALTER TABLE club ADD COLUMN last_digest_at '
                    + ('TIMESTAMP' if is_postgres else 'DATETIME')
                )

        if 'game_player' in tables:
            gp_cols = {c['name'] for c in inspector.get_columns('game_player')}
            if 'reminded_at' not in gp_cols:
                statements.append(
                    'ALTER TABLE game_player ADD COLUMN reminded_at '
                    + ('TIMESTAMP' if is_postgres else 'DATETIME')
                )
            if 'attending_at' not in gp_cols:
                statements.append(
                    'ALTER TABLE game_player ADD COLUMN attending_at '
                    + ('TIMESTAMP' if is_postgres else 'DATETIME')
                )
            if 'day_reminded_at' not in gp_cols:
                statements.append(
                    'ALTER TABLE game_player ADD COLUMN day_reminded_at '
                    + ('TIMESTAMP' if is_postgres else 'DATETIME')
                )

        if statements:
            app.logger.warning('Applying schema upgrades: %s', statements)
            with db.engine.begin() as conn:
                for statement in statements:
                    conn.execute(text(statement))
    except Exception:
        app.logger.exception('Schema upgrade failed')


def create_app(config_name=None):
    app = Flask(__name__, static_folder=None)
    app.config.from_object(get_config(config_name))

    # Never run production with a guessable signing key.
    from backend.config import DEV_FALLBACK_SECRET
    if app.config.get('APP_ENV') == 'production' and \
            app.config.get('SECRET_KEY') in (None, '', 'change-me', DEV_FALLBACK_SECRET):
        raise RuntimeError(
            'SECRET_KEY must be set to a strong value in production '
            '(the dev fallback is not allowed).'
        )

    db.init_app(app)
    _register_blueprints(app)

    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        return resp

    with app.app_context():
        _ensure_pg_schema(app)
        _migrate_legacy_schema(app)
        _clear_conflicting_legacy_indexes(app)
        _upgrade_schema(app)
        if app.config.get('RESET_DB_ON_BOOT'):
            # One-time escape hatch for migrating off an old schema:
            # set RESET_DB_ON_BOOT=true, deploy, then REMOVE the env var.
            app.logger.warning('RESET_DB_ON_BOOT set — dropping and recreating all tables')
            db.drop_all()
            db.create_all()
        elif app.config.get('AUTO_CREATE_DB'):
            db.create_all()
        _maybe_auto_seed(app)

    @app.get('/health')
    def health():
        # A cheap DB ping keeps the health check honest — Render flags 503s.
        from sqlalchemy import text
        try:
            db.session.execute(text('SELECT 1'))
            db_ok = True
        except Exception:
            app.logger.exception('Health check: database unreachable')
            db_ok = False
        return jsonify({
            'status': 'ok' if db_ok else 'degraded',
            'db': db_ok,
            'env': app.config.get('APP_ENV'),
        }), (200 if db_ok else 503)

    @app.get('/')
    def index():
        return send_from_directory(FRONTEND_DIR, 'index.html')

    # Short share links with Open Graph tags: chat apps render a rich preview
    # (crawlers can't read #hash routes), humans get bounced into the app.
    def _share_page(title, description, target_hash):
        from markupsafe import escape
        icon = request.url_root.rstrip('/') + '/icon-512.png'
        return (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<title>{escape(title)}</title>'
            f'<meta property="og:title" content="{escape(title)}">'
            f'<meta property="og:description" content="{escape(description)}">'
            f'<meta property="og:image" content="{escape(icon)}">'
            '<meta property="og:site_name" content="Third Shot">'
            '<meta name="twitter:card" content="summary">'
            f'<meta http-equiv="refresh" content="0;url=/{escape(target_hash)}">'
            f'<script>location.replace("/{escape(target_hash)}")</script>'
            '</head><body>Opening Third Shot…</body></html>'
        )

    @app.get('/g/<int:game_id>')
    def share_game(game_id):
        from backend.models import Game
        game = db.session.get(Game, game_id)
        if not game:
            return 'not found', 404
        if game.visibility == 'private':
            # Don't leak details of invite-only games to link crawlers.
            return _share_page('A pickleball game on Third Shot',
                               'Open the link to see the details.', f'#game/{game_id}')
        court = game.court.name if game.court else 'the court'
        when = game.scheduled_at.strftime('%a, %b %-d · %-I:%M %p UTC') if game.scheduled_at else ''
        return _share_page(f'Pickleball at {court}',
                           f'{when} — join on Third Shot'.strip(' —'), f'#game/{game_id}')

    @app.get('/c/<int:court_id>')
    def share_court(court_id):
        from backend.models import Court
        court = db.session.get(Court, court_id)
        if not court:
            return 'not found', 404
        bits = [f'{court.num_courts} court{"" if court.num_courts == 1 else "s"}']
        if court.city:
            bits.insert(0, court.city)
        return _share_page(court.name, ' · '.join(bits) + ' — on Third Shot', f'#court/{court_id}')

    @app.get('/u/<int:user_id>')
    def share_invite(user_id):
        from backend.models import User
        user = db.session.get(User, user_id)
        if not user or user.deleted_at:
            return 'not found', 404
        # #invite/<id> keeps the signup friend-request flow intact.
        return _share_page(f'Play pickleball with {user.display_name}',
                           'Join them on Third Shot — courts, players & games near you.',
                           f'#invite/{user_id}')

    @app.get('/cl/<int:club_id>')
    def share_club(club_id):
        from backend.models import Club
        club = db.session.get(Club, club_id)
        if not club:
            return 'not found', 404
        n = len(club.members)
        return _share_page(f'🏛 {club.name}',
                           f'{n} member{"" if n == 1 else "s"} — join the club on Third Shot',
                           f'#club/{club_id}')

    @app.get('/t/<int:tournament_id>')
    def share_tournament(tournament_id):
        from backend.models import Tournament
        t = db.session.get(Tournament, tournament_id)
        if not t:
            return 'not found', 404
        court = t.court.name if t.court else 'the court'
        return _share_page(f'🏆 {t.name}',
                           f'Pickleball tournament at {court} — register on Third Shot',
                           f'#tournament/{tournament_id}')

    @app.get('/<path:filename>')
    def frontend_assets(filename):
        return send_from_directory(FRONTEND_DIR, filename)

    return app


def _register_blueprints(app):
    from backend.routes.auth import auth_bp
    from backend.routes.chat import chat_bp
    from backend.routes.clubs import clubs_bp
    from backend.routes.leagues import leagues_bp
    from backend.routes.push import push_bp
    from backend.routes.courts import courts_bp
    from backend.routes.games import games_bp
    from backend.routes.social import social_bp
    from backend.routes.tournaments import tournaments_bp

    app.register_blueprint(auth_bp, url_prefix='/api')
    app.register_blueprint(courts_bp, url_prefix='/api')
    app.register_blueprint(games_bp, url_prefix='/api')
    app.register_blueprint(social_bp, url_prefix='/api')
    app.register_blueprint(chat_bp, url_prefix='/api')
    app.register_blueprint(tournaments_bp, url_prefix='/api')
    app.register_blueprint(clubs_bp, url_prefix='/api')
    app.register_blueprint(push_bp, url_prefix='/api')
    app.register_blueprint(leagues_bp, url_prefix='/api')


app = create_app()
