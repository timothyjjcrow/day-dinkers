"""Passwordless sign-in with a six-digit code sent by email.

The code is never stored. Requesting one returns a signed challenge that
binds the address, an expiry and a random nonce to an HMAC of the code, so
only the person who received the email can complete it. A shared rate-limit
row marks each challenge used, and per-address limits on the routes cap how
many guesses anyone gets.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time

from flask import current_app


CODE_DIGITS = 6
CODE_LIFETIME_SECONDS = 10 * 60
_CODE_RE = re.compile(rf'^\d{{{CODE_DIGITS}}}$')
_NONCE_RE = re.compile(r'^[A-Za-z0-9_-]{16,32}$')


def is_available(app=None):
    """Codes need working transactional email; tests capture messages."""
    app = app or current_app
    if app.config.get('TESTING'):
        return True
    return bool(
        str(app.config.get('RESEND_API_KEY') or '').strip()
        and str(app.config.get('TRANSACTIONAL_EMAIL_FROM') or '').strip()
    )


def _key():
    secret = str(current_app.config['SECRET_KEY']).encode('utf-8')
    return hmac.new(secret, b'third-shot-email-code-v1', hashlib.sha256).digest()


def _encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _decode(text):
    text = str(text)
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def _mac(body, code):
    return hmac.new(_key(), f'{body}.{code}'.encode('ascii'), hashlib.sha256).digest()


def issue(email, now=None):
    """Return (challenge, code, nonce) for one sign-in attempt."""
    now = int(now if now is not None else time.time())
    code = f'{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}'
    nonce = secrets.token_urlsafe(12)
    body = _encode(json.dumps(
        {'e': email, 'x': now + CODE_LIFETIME_SECONDS, 'n': nonce},
        separators=(',', ':'),
    ).encode('utf-8'))
    return f'{body}.{_encode(_mac(body, code))}', code, nonce


def verify(challenge, code, email, now=None):
    """Return (claims, error). error is None, 'invalid' or 'expired'."""
    code = str(code or '').strip()
    if not _CODE_RE.fullmatch(code):
        return None, 'invalid'
    try:
        body, supplied = str(challenge or '').split('.', 1)
        claims = json.loads(_decode(body))
        supplied_mac = _decode(supplied)
    except (ValueError, TypeError):
        return None, 'invalid'
    if not isinstance(claims, dict) or not hmac.compare_digest(_mac(body, code), supplied_mac):
        return None, 'invalid'
    if (
        claims.get('e') != email
        or not isinstance(claims.get('x'), int)
        or not _NONCE_RE.fullmatch(str(claims.get('n') or ''))
    ):
        return None, 'invalid'
    if claims['x'] <= int(now if now is not None else time.time()):
        return None, 'expired'
    return claims, None


def consume(claims, now=None):
    """Mark a verified challenge used. True for exactly one caller."""
    from backend.security import _increment_bucket

    now = now if now is not None else time.time()
    per_seconds = 3600
    # The bucket window is the hour holding the expiry, so the used marker
    # outlives the challenge and is then cleaned up with other buckets.
    window = int(claims['x'] // per_seconds)
    backend = current_app.config.get('RATE_LIMIT_BACKEND', 'memory')
    return _increment_bucket(
        f'email-code-used:{claims["n"]}', window, now, per_seconds, backend,
    ) == 1


def send(email, code, nonce):
    from html import escape

    from backend.email_delivery import send_transactional_email

    minutes = CODE_LIFETIME_SECONDS // 60
    intro = 'Enter this code in Third Shot to sign in:'
    footer = (
        f'It expires in {minutes} minutes. If you did not ask for it, '
        'you can ignore this email.'
    )
    send_transactional_email(
        to=email,
        subject=f'{code} is your Third Shot code',
        html=(
            f'<p>{escape(intro)}</p>'
            f'<p style="font-size:28px;font-weight:700;letter-spacing:6px">{code}</p>'
            f'<p>{escape(footer)}</p>'
        ),
        text=f'{intro}\n\n{code}\n\n{footer}',
        idempotency_key=f'email-code-{nonce}',
    )
