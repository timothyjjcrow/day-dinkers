"""Flask application bootstrap."""
import os
import threading

from flask import Flask, jsonify, send_from_directory
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


def create_app(config_name=None):
    app = Flask(__name__, static_folder=None)
    app.config.from_object(get_config(config_name))
    db.init_app(app)
    _register_blueprints(app)

    with app.app_context():
        if app.config.get('AUTO_CREATE_DB'):
            db.create_all()
        _maybe_auto_seed(app)

    @app.get('/health')
    def health():
        return jsonify({'status': 'ok', 'env': app.config.get('APP_ENV')})

    @app.get('/')
    def index():
        return send_from_directory(FRONTEND_DIR, 'index.html')

    @app.get('/<path:filename>')
    def frontend_assets(filename):
        return send_from_directory(FRONTEND_DIR, filename)

    return app


def _register_blueprints(app):
    from backend.routes.auth import auth_bp
    from backend.routes.chat import chat_bp
    from backend.routes.courts import courts_bp
    from backend.routes.games import games_bp
    from backend.routes.social import social_bp

    app.register_blueprint(auth_bp, url_prefix='/api')
    app.register_blueprint(courts_bp, url_prefix='/api')
    app.register_blueprint(games_bp, url_prefix='/api')
    app.register_blueprint(social_bp, url_prefix='/api')
    app.register_blueprint(chat_bp, url_prefix='/api')


app = create_app()
