import os


def _get_bool(name, default=False):
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    value = raw_value.strip().lower()
    if value in {'1', 'true', 'yes', 'on'}:
        return True
    if value in {'0', 'false', 'no', 'off'}:
        return False
    return default


def _get_int(name, default):
    raw_value = os.getenv(name)
    try:
        return int(raw_value) if raw_value is not None else default
    except (TypeError, ValueError):
        return default


def _database_url():
    """Normalize DATABASE_URL for SQLAlchemy 2 + psycopg3 (Render gives postgres://)."""
    url = os.getenv('DATABASE_URL', 'sqlite:///app.db')
    if url.startswith('postgres://'):
        url = 'postgresql+psycopg://' + url[len('postgres://'):]
    elif url.startswith('postgresql://'):
        url = 'postgresql+psycopg://' + url[len('postgresql://'):]
    return url


class BaseConfig:
    APP_ENV = os.getenv('APP_ENV', 'development')
    SECRET_KEY = os.getenv('SECRET_KEY', 'change-me')
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_ALGORITHM = os.getenv('JWT_ALGORITHM', 'HS256')
    JWT_TTL_SECONDS = _get_int('JWT_TTL_SECONDS', 60 * 60 * 24 * 30)
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
    PORT = _get_int('PORT', 8000)
    JSON_SORT_KEYS = False
    TESTING = False
    DEBUG = False
    AUTO_CREATE_DB = _get_bool('AUTO_CREATE_DB', default=False)
    AUTO_SEED_COURTS = _get_bool('AUTO_SEED_COURTS', default=False)
    PRESENCE_STALE_AFTER_SECONDS = _get_int('PRESENCE_STALE_AFTER_SECONDS', 7200)


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    AUTO_CREATE_DB = _get_bool('AUTO_CREATE_DB', default=True)


class StagingConfig(BaseConfig):
    APP_ENV = 'staging'


class ProductionConfig(BaseConfig):
    APP_ENV = 'production'


class TestingConfig(BaseConfig):
    APP_ENV = 'testing'
    TESTING = True
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.getenv('TEST_DATABASE_URL', 'sqlite:///:memory:')
    AUTO_CREATE_DB = True


CONFIG_BY_NAME = {
    'development': DevelopmentConfig,
    'staging': StagingConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
}


def get_config(name=None):
    config_name = (name or os.getenv('APP_ENV') or 'development').strip().lower()
    return CONFIG_BY_NAME.get(config_name, DevelopmentConfig)
