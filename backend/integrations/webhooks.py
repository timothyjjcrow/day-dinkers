"""Webhook signature and idempotency primitives; raw payloads are never stored."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import secrets
import time

from backend.integrations.errors import WebhookVerificationError
from backend.integrations.safety import stable_digest


@dataclass(frozen=True)
class VerifiedWebhook:
    timestamp: int
    signature_digest: str
    payload_digest: str


def generate_operator_token():
    """Return a token once and the SHA-256 value safe to keep in host config."""
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


def webhook_idempotency_key(provider_key, connection_public_id, event_id, payload):
    event = str(event_id or '').strip()
    if not event:
        event = stable_digest(payload)
    return stable_digest(
        f'{str(provider_key).lower()}:{connection_public_id}:{event}'
    )


def sign_payload(secret, payload: bytes, *, timestamp=None):
    timestamp = int(time.time() if timestamp is None else timestamp)
    key = secret.encode() if isinstance(secret, str) else secret
    signed = str(timestamp).encode('ascii') + b'.' + payload
    signature = hmac.new(key, signed, hashlib.sha256).hexdigest()
    return f't={timestamp},v1={signature}'


def verify_signature(secret, payload: bytes, header, *, tolerance=300, now=None):
    values = {}
    for part in str(header or '').split(','):
        key, separator, value = part.strip().partition('=')
        if separator and key in {'t', 'v1'}:
            values.setdefault(key, []).append(value)
    try:
        timestamp = int(values['t'][0])
    except (KeyError, TypeError, ValueError):
        raise WebhookVerificationError()
    current = int(time.time() if now is None else now)
    if abs(current - timestamp) > min(max(int(tolerance), 30), 900):
        raise WebhookVerificationError('webhook_timestamp_outside_tolerance')
    key = secret.encode() if isinstance(secret, str) else secret
    signed = str(timestamp).encode('ascii') + b'.' + payload
    expected = hmac.new(key, signed, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in values.get('v1', [])):
        raise WebhookVerificationError()
    return VerifiedWebhook(
        timestamp=timestamp,
        signature_digest=stable_digest(expected),
        payload_digest=stable_digest(payload),
    )
