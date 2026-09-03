"""Signed OAuth state and exchange result contracts.

No vendor OAuth adapter is active yet. These primitives prevent a future
adapter from inventing ad-hoc state handling or returning raw tokens to SQL.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import hmac
import json
import secrets
import time

from backend.integrations.errors import ValidationError
from backend.integrations.safety import (
    sanitize_public_config,
    validate_opaque_reference,
)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')


def _b64decode(value: str) -> bytes:
    decoded = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
    # Reject alternate/non-canonical encodings that decode to the same bytes;
    # signed state should have one stable textual representation.
    if _b64encode(decoded) != value:
        raise ValueError('non_canonical_base64url')
    return decoded


@dataclass(frozen=True)
class OAuthState:
    business_id: int
    provider_key: str
    redirect_path: str
    nonce: str
    expires_at: int


@dataclass(frozen=True)
class OAuthExchangeResult:
    external_account_id: str
    credential_ref: str
    public_config: dict
    capabilities: tuple[str, ...] = ()

    def __post_init__(self):
        validate_opaque_reference(self.credential_ref, required=True)
        sanitize_public_config(self.public_config)


class OAuthStateManager:
    def __init__(self, signing_key, *, ttl_seconds=600, clock=time.time):
        key = signing_key.encode() if isinstance(signing_key, str) else signing_key
        if not key or len(key) < 32:
            raise ValueError('OAuth state signing key must be at least 32 bytes')
        self._key = key
        self._ttl = min(max(int(ttl_seconds), 60), 1800)
        self._clock = clock

    def issue(self, *, business_id, provider_key, redirect_path='/'):
        if not str(redirect_path).startswith('/') or str(redirect_path).startswith('//'):
            raise ValidationError('invalid_oauth_redirect_path')
        payload = {
            'business_id': int(business_id),
            'provider_key': str(provider_key).strip().lower(),
            'redirect_path': str(redirect_path),
            'nonce': secrets.token_urlsafe(18),
            'exp': int(self._clock()) + self._ttl,
        }
        encoded = _b64encode(json.dumps(
            payload, sort_keys=True, separators=(',', ':'),
        ).encode('utf-8'))
        signature = _b64encode(hmac.new(
            self._key, encoded.encode('ascii'), hashlib.sha256,
        ).digest())
        return f'{encoded}.{signature}'

    def verify(self, state):
        try:
            encoded, signature = str(state).split('.', 1)
            expected = hmac.new(
                self._key, encoded.encode('ascii'), hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(expected, _b64decode(signature)):
                raise ValidationError('invalid_oauth_state')
            payload = json.loads(_b64decode(encoded))
            expires_at = int(payload['exp'])
            if expires_at < int(self._clock()):
                raise ValidationError('oauth_state_expired')
            return OAuthState(
                business_id=int(payload['business_id']),
                provider_key=str(payload['provider_key']),
                redirect_path=str(payload['redirect_path']),
                nonce=str(payload['nonce']),
                expires_at=expires_at,
            )
        except ValidationError:
            raise
        except Exception:
            raise ValidationError('invalid_oauth_state')
