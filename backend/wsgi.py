"""WSGI entrypoint used by Render/Gunicorn."""
import os

from backend.app import create_app
from backend.services.court_seeder import seed_courts


def _env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


config_name = os.environ.get('FLASK_ENV', 'production')
app = create_app(config_name)

if _env_bool('AUTO_SEED_COURTS', False):
    with app.app_context():
        seeded = seed_courts()
        if seeded:
            print(f"Seeded {seeded} courts")
