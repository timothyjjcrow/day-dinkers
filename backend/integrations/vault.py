"""Credential-vault implementations that never persist plaintext secrets."""
from __future__ import annotations

import os
import re
from typing import Protocol
import uuid

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

from backend.app import db
from backend.integrations.errors import CredentialVaultUnavailable, ValidationError
from backend.integrations.safety import validate_opaque_reference
from backend.models import utcnow


_ENV_NAME_RE = re.compile(r'^BUSINESS_PROVIDER_SECRET_[A-Z0-9_]{1,96}$')
_PURPOSES = frozenset({'credential', 'webhook', 'cursor'})


class CredentialVault(Protocol):
    def put(self, secret, *, purpose: str, created_by_id: int) -> str: ...
    def resolve(self, reference: str) -> bytes: ...
    def delete(self, reference: str) -> None: ...


class DisabledCredentialVault:
    def put(self, secret, *, purpose, created_by_id):
        raise CredentialVaultUnavailable()

    def resolve(self, reference):
        raise CredentialVaultUnavailable()

    def delete(self, reference):
        raise CredentialVaultUnavailable()


class EnvironmentReferenceVault:
    """Read secrets provisioned by the host through an opaque ``env://`` ref."""
    prefix = 'env://'

    def put(self, secret, *, purpose, created_by_id):
        raise CredentialVaultUnavailable('environment_vault_is_read_only')

    def resolve(self, reference):
        reference = validate_opaque_reference(reference, required=True)
        if not reference.startswith(self.prefix):
            raise CredentialVaultUnavailable('unsupported_credential_vault')
        name = reference[len(self.prefix):]
        if not _ENV_NAME_RE.fullmatch(name):
            raise CredentialVaultUnavailable('invalid_environment_secret_reference')
        value = os.getenv(name)
        if not value:
            raise CredentialVaultUnavailable('credential_reference_unavailable')
        return value.encode('utf-8')

    def delete(self, reference):
        # Host-managed environment values cannot be deleted by app code. The
        # connection may detach immediately; operators rotate/remove the value.
        reference = validate_opaque_reference(reference, required=True)
        if not reference.startswith(self.prefix) or not _ENV_NAME_RE.fullmatch(
            reference[len(self.prefix):]
        ):
            raise CredentialVaultUnavailable('invalid_environment_secret_reference')


def _key_version():
    try:
        version = int(current_app.config.get('BUSINESS_CREDENTIAL_KEY_VERSION', 1))
    except (TypeError, ValueError):
        raise CredentialVaultUnavailable('credential_key_version_invalid')
    if version < 1:
        raise CredentialVaultUnavailable('credential_key_version_invalid')
    return version


def _fernet_for_version(version):
    current_version = _key_version()
    if version == current_version:
        raw = current_app.config.get('BUSINESS_CREDENTIAL_ENCRYPTION_KEY', '')
    else:
        # Previous keys are retained only while rows encrypted by that version
        # are being rotated to the current key.
        raw = os.getenv(f'BUSINESS_CREDENTIAL_ENCRYPTION_KEY_V{version}', '')
    key = str(raw or '').strip()
    if not key:
        raise CredentialVaultUnavailable('credential_encryption_key_unavailable')
    try:
        return Fernet(key.encode('ascii'))
    except (TypeError, ValueError, UnicodeEncodeError):
        raise CredentialVaultUnavailable('credential_encryption_key_invalid')


def _secret_bytes(secret):
    if isinstance(secret, str):
        value = secret.encode('utf-8')
    elif isinstance(secret, bytes):
        value = secret
    else:
        raise ValidationError('credential_secret_required')
    if not value:
        raise ValidationError('credential_secret_required')
    if len(value) > 65_536:
        raise ValidationError('credential_secret_too_large')
    return value


def _vault_public_id(reference):
    reference = validate_opaque_reference(reference, required=True)
    if not reference.startswith('vault://'):
        raise CredentialVaultUnavailable('unsupported_credential_vault')
    candidate = reference[len('vault://'):]
    try:
        return str(uuid.UUID(candidate))
    except (ValueError, TypeError, AttributeError):
        raise CredentialVaultUnavailable('invalid_credential_reference')


class EncryptedSqlCredentialVault:
    """Fernet-encrypted SQL vault addressed by random ``vault://`` refs.

    The caller owns the surrounding database transaction. This keeps secret
    creation and attaching the resulting reference to a connection atomic.
    """
    prefix = 'vault://'

    def put(self, secret, *, purpose, created_by_id):
        from backend.integrations.models import BusinessCredentialSecret

        purpose = str(purpose or '').strip().lower()
        if purpose not in _PURPOSES:
            raise ValidationError('invalid_credential_purpose')
        try:
            actor_id = int(created_by_id)
        except (TypeError, ValueError):
            raise ValidationError('credential_actor_required')
        version = _key_version()
        ciphertext = _fernet_for_version(version).encrypt(
            _secret_bytes(secret),
        ).decode('ascii')
        row = BusinessCredentialSecret(
            purpose=purpose,
            ciphertext=ciphertext,
            key_version=version,
            created_by_id=actor_id,
        )
        db.session.add(row)
        db.session.flush()
        return row.reference

    def _active_row(self, reference, *, lock=False):
        from backend.integrations.models import BusinessCredentialSecret

        public_id = _vault_public_id(reference)
        query = BusinessCredentialSecret.query.filter_by(
            public_id=public_id, deleted_at=None,
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        row = query.first()
        if row is None or not row.ciphertext:
            raise CredentialVaultUnavailable('credential_reference_unavailable')
        return row

    def resolve(self, reference):
        row = self._active_row(reference)
        try:
            plaintext = _fernet_for_version(row.key_version).decrypt(
                row.ciphertext.encode('ascii'),
            )
        except (InvalidToken, UnicodeEncodeError, ValueError):
            raise CredentialVaultUnavailable('credential_decryption_failed')
        row.last_accessed_at = utcnow()
        return plaintext

    def delete(self, reference):
        row = self._active_row(reference, lock=True)
        row.ciphertext = ''
        row.deleted_at = utcnow()

    def rotate(self, reference):
        row = self._active_row(reference, lock=True)
        try:
            plaintext = _fernet_for_version(row.key_version).decrypt(
                row.ciphertext.encode('ascii'),
            )
        except (InvalidToken, UnicodeEncodeError, ValueError):
            raise CredentialVaultUnavailable('credential_decryption_failed')
        version = _key_version()
        row.ciphertext = _fernet_for_version(version).encrypt(plaintext).decode('ascii')
        row.key_version = version
        return row.reference


class RoutingCredentialVault:
    """Resolve host-managed ``env://`` and writable ``vault://`` references."""

    def __init__(self):
        self.environment = EnvironmentReferenceVault()
        self.encrypted_sql = EncryptedSqlCredentialVault()

    def put(self, secret, *, purpose, created_by_id):
        return self.encrypted_sql.put(
            secret, purpose=purpose, created_by_id=created_by_id,
        )

    def resolve(self, reference):
        reference = validate_opaque_reference(reference, required=True)
        if reference.startswith('env://'):
            return self.environment.resolve(reference)
        if reference.startswith('vault://'):
            return self.encrypted_sql.resolve(reference)
        raise CredentialVaultUnavailable('unsupported_credential_vault')

    def delete(self, reference):
        reference = validate_opaque_reference(reference, required=True)
        if reference.startswith('env://'):
            return self.environment.delete(reference)
        if reference.startswith('vault://'):
            return self.encrypted_sql.delete(reference)
        raise CredentialVaultUnavailable('unsupported_credential_vault')


def configured_vault():
    mode = str(
        current_app.config.get('BUSINESS_CREDENTIAL_VAULT') or 'disabled'
    ).strip().lower()
    if mode == 'environment_reference':
        return EnvironmentReferenceVault()
    if mode in {'encrypted_sql', 'hybrid'}:
        # Each encrypted operation validates the key and fails closed if the
        # configuration is missing or malformed.
        return RoutingCredentialVault()
    return DisabledCredentialVault()
