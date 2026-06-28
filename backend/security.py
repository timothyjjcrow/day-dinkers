"""Lightweight, dependency-free security helpers: per-IP rate limiting.

The limiter is in-process (fine for the single-worker gunicorn deploy) and is
disabled under TESTING so the test suite can hammer endpoints freely.
"""
import time
from functools import wraps

from flask import current_app, jsonify, request

# (endpoint:ip, window_index) -> request count for that fixed window
_BUCKETS = {}
_MAX_BUCKETS = 10000


def client_ip():
    """Best-effort client IP, honoring the proxy header Render/most hosts set."""
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
            key = (f'{request.endpoint}:{client_ip()}', window)
            count = _BUCKETS.get(key, 0) + 1
            _BUCKETS[key] = count
            if len(_BUCKETS) > _MAX_BUCKETS:
                for stale in [k for k in _BUCKETS if k[1] < window]:
                    _BUCKETS.pop(stale, None)
            if count > limit:
                retry = int((window + 1) * per_seconds - now)
                resp = jsonify({'error': 'rate_limited', 'retry_after': retry})
                resp.headers['Retry-After'] = str(max(1, retry))
                return resp, 429
            return view(*args, **kwargs)
        return wrapped
    return decorator
