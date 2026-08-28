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


# The app keeps all of its tables in a dedicated Postgres schema so it can
# never collide with tables left behind by older deployments in `public`.
PG_SCHEMA = 'picklepals'

# Dev/test fallback only — production refuses to boot with it (see create_app).
# HS256 wants ≥32 bytes, so keep it long enough not to trip key-length warnings.
DEV_FALLBACK_SECRET = 'change-me-dev-only-not-a-real-secret-key'


def _database_url():
    """Normalize common hosted Postgres URLs for SQLAlchemy 2 + psycopg3."""
    url = os.getenv('DATABASE_URL', 'sqlite:///app.db')
    if url.startswith('postgres://'):
        url = 'postgresql+psycopg://' + url[len('postgres://'):]
    elif url.startswith('postgresql://'):
        url = 'postgresql+psycopg://' + url[len('postgresql://'):]
    return url


def _is_pooled_database_url(url):
    """Whether this is a Neon/PgBouncer pooled endpoint."""
    return '-pooler.' in str(url or '').lower()


def _engine_options():
    url = _database_url()
    if not url.startswith('postgresql'):
        return {}
    options = {
        'pool_pre_ping': True,
        # Neon Free suspends idle computes. Recycle warm-instance connections
        # before they can outlive the compute that accepted them.
        'pool_recycle': 300,
        'pool_size': 2,
        'max_overflow': 3,
    }
    if not _is_pooled_database_url(url):
        # Direct/admin connections can set search_path at startup. Neon pooled
        # URLs reject this option, so their role gets a persistent search_path
        # during the one-time recovery migration instead.
        options['connect_args'] = {'options': f'-csearch_path={PG_SCHEMA}'}
    return options


class BaseConfig:
    SERVERLESS_RUNTIME = _get_bool(
        'SERVERLESS_RUNTIME', default=os.getenv('VERCEL') == '1',
    )
    APP_ENV = os.getenv('APP_ENV', 'development')
    SECRET_KEY = os.getenv('SECRET_KEY', DEV_FALLBACK_SECRET)
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_ENGINE_OPTIONS = _engine_options()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_ALGORITHM = os.getenv('JWT_ALGORITHM', 'HS256')
    JWT_TTL_SECONDS = _get_int('JWT_TTL_SECONDS', 60 * 60 * 24 * 30)
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
    PORT = _get_int('PORT', 8000)
    JSON_SORT_KEYS = False
    TESTING = False
    DEBUG = False
    SCHEMA_MANAGEMENT_ENABLED = _get_bool(
        'SCHEMA_MANAGEMENT_ENABLED', default=not SERVERLESS_RUNTIME,
    )
    AUTO_CREATE_DB = _get_bool(
        'AUTO_CREATE_DB', default=not SERVERLESS_RUNTIME,
    )
    AUTO_SEED_COURTS = _get_bool('AUTO_SEED_COURTS', default=False)
    RESET_DB_ON_BOOT = _get_bool('RESET_DB_ON_BOOT', default=False)
    PRESENCE_STALE_AFTER_SECONDS = _get_int('PRESENCE_STALE_AFTER_SECONDS', 7200)
    RATE_LIMIT_ENABLED = _get_bool('RATE_LIMIT_ENABLED', default=True)
    RATE_LIMIT_BACKEND = os.getenv(
        'RATE_LIMIT_BACKEND',
        'database' if SERVERLESS_RUNTIME else 'memory',
    ).strip().lower()
    # Largest legitimate request is a court-photo upload (~500KB image → ~700KB
    # base64 JSON); cap everything at 2MB so oversized bodies get 413s.
    MAX_CONTENT_LENGTH = _get_int('MAX_CONTENT_LENGTH', 2 * 1024 * 1024)
    # Web push — dark until both VAPID keys are set in the environment.
    VAPID_PRIVATE_KEY = os.getenv('VAPID_PRIVATE_KEY', '')
    VAPID_PUBLIC_KEY = os.getenv('VAPID_PUBLIC_KEY', '')
    VAPID_CLAIMS_EMAIL = os.getenv('VAPID_CLAIMS_EMAIL', 'mailto:timothyjjcrow@gmail.com')
    PUSH_DELIVERY_ENABLED = _get_bool(
        'PUSH_DELIVERY_ENABLED', default=not SERVERLESS_RUNTIME,
    )


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
    # A single shared connection so an in-memory SQLite DB is consistent across
    # app contexts/requests within a test (otherwise pooled connections each get
    # their own empty :memory: database).
    from sqlalchemy.pool import StaticPool
    SQLALCHEMY_ENGINE_OPTIONS = {
        'poolclass': StaticPool,
        'connect_args': {'check_same_thread': False},
    }
    AUTO_CREATE_DB = True
    SCHEMA_MANAGEMENT_ENABLED = True
    SERVERLESS_RUNTIME = False
    RATE_LIMIT_ENABLED = False
    RATE_LIMIT_BACKEND = 'memory'
    PUSH_DELIVERY_ENABLED = True


CONFIG_BY_NAME = {
    'development': DevelopmentConfig,
    'staging': StagingConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
}


def get_config(name=None):
    config_name = (
        name or os.getenv('APP_ENV') or os.getenv('FLASK_ENV') or 'development'
    ).strip().lower()
    return CONFIG_BY_NAME.get(config_name, DevelopmentConfig)
