import os

basedir = os.path.abspath(os.path.dirname(__file__))


def _env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def _env_int(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _env_float(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _normalize_database_url(raw_url):
    if not raw_url:
        return raw_url
    if raw_url.startswith('postgres://'):
        return raw_url.replace('postgres://', 'postgresql://', 1)
    return raw_url


class BaseConfig:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-prod')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_EXPIRATION_HOURS = 24
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
    ADMIN_EMAILS = os.environ.get('ADMIN_EMAILS', '')
    COURT_UPDATE_REVIEWER_EMAILS = os.environ.get('COURT_UPDATE_REVIEWER_EMAILS', '')
    COURT_UPDATE_REVIEWER_USERNAMES = os.environ.get('COURT_UPDATE_REVIEWER_USERNAMES', '')
    COURT_UPDATE_AUTO_APPLY = _env_bool('COURT_UPDATE_AUTO_APPLY', False)
    COURT_UPDATE_AUTO_APPLY_THRESHOLD = _env_float('COURT_UPDATE_AUTO_APPLY_THRESHOLD', 0.92)
    COURT_UPDATE_MAX_IMAGES = _env_int('COURT_UPDATE_MAX_IMAGES', 8)
    COURT_UPDATE_MAX_EVENTS = _env_int('COURT_UPDATE_MAX_EVENTS', 6)
    COURT_UPDATE_MAX_IMAGE_BYTES = _env_int('COURT_UPDATE_MAX_IMAGE_BYTES', 2 * 1024 * 1024)
    CORS_ALLOWED_ORIGINS = os.environ.get('CORS_ALLOWED_ORIGINS', '*')
    SOCKETIO_ASYNC_MODE = os.environ.get('SOCKETIO_ASYNC_MODE', 'threading')


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = _normalize_database_url(
        os.environ.get(
            'DATABASE_URL',
            'sqlite:///' + os.path.join(basedir, '..', 'pickleball_dev.db')
        )
    )


class TestingConfig(BaseConfig):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'


class ProductionConfig(BaseConfig):
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = _normalize_database_url(os.environ.get('DATABASE_URL'))


config = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
}
