"""Transactional email delivery is fail-closed and network-free in tests."""
import pytest

from backend.app import create_app
from backend.email_delivery import send_transactional_email


@pytest.fixture()
def app():
    app = create_app('testing')
    app.config['TRANSACTIONAL_EMAIL_FROM'] = 'Third Shot <hello@example.com>'
    return app


def test_testing_delivery_is_captured_with_idempotency(app):
    with app.app_context():
        result = send_transactional_email(
            to='Manager@Example.com',
            subject='Confirm your venue',
            html='<p>Your code is 123456.</p>',
            text='Your code is 123456.',
            idempotency_key='business-verification-12-attempt-1',
        )

        assert result.captured is True
        assert result.provider == 'test_outbox'
        assert app.extensions['email_outbox'] == [{
            'id': 'test-email-1',
            'idempotency_key': 'business-verification-12-attempt-1',
            'from': 'Third Shot <hello@example.com>',
            'to': ['manager@example.com'],
            'subject': 'Confirm your venue',
            'html': '<p>Your code is 123456.</p>',
            'text': 'Your code is 123456.',
        }]


@pytest.mark.parametrize('recipient', ['', 'missing-at.example.com', 'a@example.com\nBcc:x@y.com'])
def test_invalid_recipient_is_rejected_before_capture(app, recipient):
    with app.app_context(), pytest.raises(ValueError):
        send_transactional_email(
            to=recipient,
            subject='Confirm',
            text='Code',
            idempotency_key='verification-1',
        )
    assert not app.extensions.get('email_outbox')
