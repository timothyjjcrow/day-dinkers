"""Encrypted TOTP MFA and one-time recovery-code primitives."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import struct
import time
from urllib.parse import quote, urlencode

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app
from werkzeug.security import check_password_hash, generate_password_hash


class MFAError(ValueError):
    pass


def _fernet():
    key = str(current_app.config.get('MFA_ENCRYPTION_KEY') or '').strip()
    if not key:
        raise MFAError('mfa_encryption_unavailable')
    try:
        return Fernet(key.encode('ascii'))
    except (ValueError, TypeError):
        raise MFAError('mfa_encryption_unavailable')


def new_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode('ascii').rstrip('=')


def encrypt_secret(secret):
    return _fernet().encrypt(str(secret).encode('ascii')).decode('ascii')


def decrypt_secret(encrypted):
    try:
        return _fernet().decrypt(str(encrypted).encode('ascii')).decode('ascii')
    except (InvalidToken, UnicodeError, ValueError):
        raise MFAError('mfa_secret_unavailable')


def _totp_at(secret, timestamp):
    padded = secret.upper() + '=' * ((8 - len(secret) % 8) % 8)
    try:
        key = base64.b32decode(padded, casefold=True)
    except Exception:
        raise MFAError('mfa_secret_unavailable')
    counter = int(timestamp // 30)
    digest = hmac.new(key, struct.pack('>Q', counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f'{value % 1_000_000:06d}'


def verify_totp(secret, code, *, now=None):
    candidate = ''.join(character for character in str(code or '') if character.isdigit())
    if len(candidate) != 6:
        return False
    now = time.time() if now is None else float(now)
    return any(
        hmac.compare_digest(candidate, _totp_at(secret, now + offset * 30))
        for offset in (-1, 0, 1)
    )


def otpauth_uri(secret, email):
    issuer = str(current_app.config.get('MFA_ISSUER') or 'Third Shot')[:64]
    label = quote(f'{issuer}:{email}', safe='')
    query = urlencode({
        'secret': secret,
        'issuer': issuer,
        'algorithm': 'SHA1',
        'digits': 6,
        'period': 30,
    })
    return f'otpauth://totp/{label}?{query}'


def new_recovery_codes(count=10):
    codes = []
    for _ in range(count):
        raw = secrets.token_hex(8)
        codes.append('-'.join(raw[index:index + 4] for index in range(0, 16, 4)))
    return codes


def hash_recovery_codes(codes):
    return json.dumps([
        generate_password_hash(code.lower()) for code in codes
    ])


def consume_recovery_code(serialized, candidate):
    normalized = str(candidate or '').strip().lower()
    if not normalized:
        return False, serialized or '[]'
    try:
        values = json.loads(serialized or '[]')
    except (TypeError, ValueError):
        values = []
    if not isinstance(values, list):
        values = []
    for index, value in enumerate(values):
        if isinstance(value, str) and check_password_hash(value, normalized):
            del values[index]
            return True, json.dumps(values)
    return False, json.dumps(values)


def verify_user_mfa(user, code, *, allow_recovery=True):
    if not user.mfa_enabled or not user.mfa_secret_encrypted:
        return False, False
    if verify_totp(decrypt_secret(user.mfa_secret_encrypted), code):
        return True, False
    if allow_recovery and re.fullmatch(
        r'[0-9a-fA-F]{4}(?:-[0-9a-fA-F]{4}){3}', str(code or '').strip(),
    ):
        valid, remaining = consume_recovery_code(user.mfa_recovery_codes, code)
        if valid:
            user.mfa_recovery_codes = remaining
            return True, True
    return False, False
