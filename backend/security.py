"""Security helpers: per-IP fixed-window rate limiting.

Local/container development can use memory. Stateless deployments use an
atomic database counter shared by every function instance.
"""
import hashlib
import time
from datetime import UTC, datetime
from functools import wraps

from flask import current_app, jsonify, request

# (endpoint:ip, window_index) -> request count for that fixed window
_BUCKETS = {}
_MAX_BUCKETS = 10000


def _database_bucket_count(bucket_key, window, now, per_seconds):
    """Atomically increment one shared rate-limit bucket."""
    from backend.app import db
    from backend.models import RateLimitBucket

    table = RateLimitBucket.__table__
    expires_at = datetime.fromtimestamp(
        (window + 1) * per_seconds, tz=UTC,
    ).replace(tzinfo=None)
    now_at = datetime.fromtimestamp(now, tz=UTC).replace(tzinfo=None)
    dialect = db.engine.dialect.name
    if dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError(f'Unsupported rate-limit database: {dialect}')

    statement = (
        insert(table)
        .values(
            bucket_key=bucket_key,
            window_id=window,
            count=1,
            expires_at=expires_at,
        )
        .on_conflict_do_update(
            index_elements=[table.c.bucket_key, table.c.window_id],
            set_={
                'count': table.c.count + 1,
                'expires_at': expires_at,
            },
        )
        .returning(table.c.count)
    )
    with db.engine.begin() as connection:
        count = connection.scalar(statement)
        if count == 1:
            connection.execute(
                table.delete().where(table.c.expires_at < now_at)
            )
    return count


def client_ip():
    """Best-effort client IP, honoring the proxy header most hosts set."""
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr or 'unknown'


def rate_limit(limit, per_seconds):
    """Allow at most `limit` requests per `per_seconds` window, per IP+endpoint."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_app.config.get('RATE_LIMIT_ENABLED', True):
                return view(*args, **kwargs)
            now = time.time()
            window = int(now // per_seconds)
            identity = f'{request.endpoint}:{client_ip()}'
            backend = current_app.config.get('RATE_LIMIT_BACKEND', 'memory')
            if backend == 'database':
                bucket_key = hashlib.sha256(identity.encode('utf-8')).hexdigest()
                try:
                    count = _database_bucket_count(
                        bucket_key, window, now, per_seconds,
                    )
                except Exception:
                    current_app.logger.exception(
                        'Shared rate-limit counter unavailable'
                    )
                    return jsonify({'error': 'service_unavailable'}), 503
            elif backend == 'memory':
                key = (identity, window)
                count = _BUCKETS.get(key, 0) + 1
                _BUCKETS[key] = count
                if len(_BUCKETS) > _MAX_BUCKETS:
                    for stale in [k for k in _BUCKETS if k[1] < window]:
                        _BUCKETS.pop(stale, None)
            else:
                current_app.logger.error(
                    'Unknown RATE_LIMIT_BACKEND: %s', backend,
                )
                return jsonify({'error': 'service_unavailable'}), 503
            if count > limit:
                retry = int((window + 1) * per_seconds - now)
                resp = jsonify({'error': 'rate_limited', 'retry_after': retry})
                resp.headers['Retry-After'] = str(max(1, retry))
                return resp, 429
            return view(*args, **kwargs)
        return wrapped
    return decorator
