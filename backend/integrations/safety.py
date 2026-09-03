"""Validation and redaction rules shared by every integration adapter."""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit

from backend.integrations.errors import ValidationError


_SECRET_KEY_PARTS = {
    'access_token', 'api_key', 'apikey', 'authorization', 'client_secret',
    'credential', 'password', 'private_key', 'refresh_token', 'secret', 'token',
}
_OPAQUE_REF_RE = re.compile(
    r'^(?:env|vault|kms)://[A-Za-z0-9][A-Za-z0-9_./:@+-]{1,240}$'
)
_BEARER_RE = re.compile(r'\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}', re.I)
_TOKENISH_RE = re.compile(r'^[A-Za-z0-9_~+/=-]{40,}$')


def stable_digest(value) -> str:
    if not isinstance(value, (str, bytes)):
        value = json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)
    if isinstance(value, str):
        value = value.encode('utf-8')
    return hashlib.sha256(value).hexdigest()


def safe_external_url(value, *, required=False) -> str:
    """Return a canonical HTTPS link; integrations never persist HTTP links."""
    raw = str(value or '').strip()
    if not raw:
        if required:
            raise ValidationError('url_required')
        return ''
    if '://' not in raw:
        raw = f'https://{raw}'
    parsed = urlsplit(raw)
    if (
        parsed.scheme.lower() != 'https'
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or any(character.isspace() for character in parsed.netloc)
    ):
        raise ValidationError('https_url_required')
    return urlunsplit((
        'https', parsed.netloc.lower(), parsed.path or '', parsed.query, parsed.fragment,
    ))


def validate_opaque_reference(value, *, required=False) -> str:
    """Accept only a vault locator, never secret material itself."""
    ref = str(value or '').strip()
    if not ref:
        if required:
            raise ValidationError('credential_reference_required')
        return ''
    if not _OPAQUE_REF_RE.fullmatch(ref):
        raise ValidationError('invalid_credential_reference')
    return ref


def _looks_secret_key(key) -> bool:
    normalized = str(key).strip().lower().replace('-', '_')
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def _looks_secret_value(value) -> bool:
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return bool(_BEARER_RE.search(stripped) or _TOKENISH_RE.fullmatch(stripped))


def sanitize_public_config(value, *, maximum_bytes=16_000):
    """Validate JSON config that is explicitly safe to store in plaintext.

    Key names commonly used for credentials and token-like values are rejected
    at every nesting level.  Actual credentials belong behind an opaque vault
    reference on the connection record.
    """
    if value in (None, ''):
        return {}
    if not isinstance(value, dict):
        raise ValidationError('connection_config_must_be_an_object')

    def clean(node, depth=0):
        if depth > 8:
            raise ValidationError('connection_config_too_deep')
        if isinstance(node, dict):
            output = {}
            for raw_key, raw_value in node.items():
                key = str(raw_key).strip()
                if not key or len(key) > 80:
                    raise ValidationError('invalid_connection_config_key')
                if _looks_secret_key(key):
                    raise ValidationError('secrets_must_use_credential_vault')
                output[key] = clean(raw_value, depth + 1)
            return output
        if isinstance(node, list):
            if len(node) > 200:
                raise ValidationError('connection_config_too_large')
            return [clean(item, depth + 1) for item in node]
        if node is None or isinstance(node, (bool, int, float)):
            return node
        text = str(node).strip()
        if len(text) > 2000:
            raise ValidationError('connection_config_value_too_long')
        if _looks_secret_value(text):
            raise ValidationError('secrets_must_use_credential_vault')
        return text

    cleaned = clean(value)
    encoded = json.dumps(cleaned, sort_keys=True, separators=(',', ':'))
    if len(encoded.encode('utf-8')) > maximum_bytes:
        raise ValidationError('connection_config_too_large')
    return cleaned


def safe_json_dumps(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)


def safe_json_loads(value, default):
    try:
        parsed = json.loads(value or '')
    except (TypeError, ValueError):
        return default
    return parsed
