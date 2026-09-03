"""Typed, safe-to-return failures for provider operations."""


class IntegrationError(ValueError):
    code = 'integration_error'
    retryable = False

    def __init__(self, code=None, message=None):
        self.code = str(code or self.code)
        self.safe_message = str(message or self.code)
        super().__init__(self.code)


class ProviderNotAvailable(IntegrationError):
    code = 'provider_not_available'


class ValidationError(IntegrationError):
    code = 'invalid_provider_payload'


class RetryableProviderError(IntegrationError):
    code = 'provider_temporarily_unavailable'
    retryable = True


class CredentialVaultUnavailable(IntegrationError):
    code = 'credential_vault_unavailable'


class WebhookVerificationError(IntegrationError):
    code = 'invalid_webhook_signature'


class OperatorAuthenticationError(IntegrationError):
    code = 'operator_authentication_required'
