"""Security helpers: per-account limits plus a broad per-IP abuse ceiling.

Local/container development can use memory. Stateless deployments use an
atomic database counter shared by every function instance.
"""
import hashlib
import ipaddress
import time
from datetime import UTC, datetime
from functools import wraps

import jwt
from flask import current_app, g, jsonify, request

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
    """Return the client address without trusting an arbitrary XFF prefix."""
    forwarded = request.headers.get('X-Forwarded-For', '')
    try:
        trusted_hops = max(0, int(current_app.config.get(
            'TRUSTED_PROXY_HOPS', 0,
        )))
    except (TypeError, ValueError):
        trusted_hops = 0
    candidate = request.remote_addr or 'unknown'
    if forwarded and trusted_hops:
        chain = [part.strip() for part in forwarded.split(',') if part.strip()]
        if len(chain) >= trusted_hops:
            candidate = chain[-trusted_hops]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return request.remote_addr or 'unknown'


def _verified_bearer_subject():
    """Read a signed JWT subject for throttling, without authenticating it."""
    current = getattr(g, 'current_user', None)
    if current is not None and getattr(current, 'id', None):
        return int(current.id)
    header = str(request.headers.get('Authorization') or '').strip()
    if not header.startswith('Bearer '):
        return None
    token = header.split(' ', 1)[1].strip()
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            current_app.config['SECRET_KEY'],
            algorithms=[current_app.config.get('JWT_ALGORITHM', 'HS256')],
        )
        user_id = int(payload.get('user_id'))
    except (Exception, TypeError, ValueError):
        return None
    return user_id if user_id > 0 else None


def _increment_bucket(identity, window, now, per_seconds, backend):
    """Increment one memory/shared bucket and return its new count."""
    if backend == 'database':
        bucket_key = hashlib.sha256(identity.encode('utf-8')).hexdigest()
        return _database_bucket_count(bucket_key, window, now, per_seconds)
    if backend == 'memory':
        key = (identity, window)
        count = _BUCKETS.get(key, 0) + 1
        _BUCKETS[key] = count
        if len(_BUCKETS) > _MAX_BUCKETS:
            for stale in [item for item in _BUCKETS if item[1] < window]:
                _BUCKETS.pop(stale, None)
        return count
    raise RuntimeError(f'Unknown RATE_LIMIT_BACKEND: {backend}')


def rate_limit(limit, per_seconds, key_func=None):
    """Limit signed-in users independently, retaining a broad IP ceiling."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_app.config.get('RATE_LIMIT_ENABLED', True):
                return view(*args, **kwargs)
            now = time.time()
            window = int(now // per_seconds)
            custom_key = key_func() if callable(key_func) else ''
            subject = _verified_bearer_subject()
            address = client_ip()
            if custom_key:
                scope = f'custom:{custom_key}'
            elif subject:
                scope = f'user:{subject}'
            else:
                scope = f'ip:{address}'
            identity = f'{request.endpoint}:{scope}'
            backend = current_app.config.get('RATE_LIMIT_BACKEND', 'memory')
            try:
                count = _increment_bucket(
                    identity, window, now, per_seconds, backend,
                )
                ip_count = 0
                if subject or custom_key:
                    ip_count = _increment_bucket(
                        f'{request.endpoint}:ip-ceiling:{address}',
                        window, now, per_seconds, backend,
                    )
            except Exception:
                current_app.logger.exception(
                    'Shared rate-limit counter unavailable'
                )
                return jsonify({'error': 'service_unavailable'}), 503
            try:
                multiplier = max(2, int(current_app.config.get(
                    'RATE_LIMIT_IP_CEILING_MULTIPLIER', 10,
                )))
            except (TypeError, ValueError):
                multiplier = 10
            ip_ceiling = max(limit + 20, limit * multiplier)
            if count > limit or ip_count > ip_ceiling:
                retry = int((window + 1) * per_seconds - now)
                resp = jsonify({'error': 'rate_limited', 'retry_after': retry})
                resp.headers['Retry-After'] = str(max(1, retry))
                return resp, 429
            return view(*args, **kwargs)
        return wrapped
    return decorator
