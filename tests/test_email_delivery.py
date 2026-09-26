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


class FakeSMTP:
    """Records one SMTP conversation instead of touching the network."""

    instances = []
    fail_login = False

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.implicit_tls = context is not None
        self.calls = []
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append('quit')
        return False

    def starttls(self, context=None):
        self.calls.append('starttls')

    def login(self, username, password):
        self.calls.append(('login', username, password))
        if FakeSMTP.fail_login:
            import smtplib
            raise smtplib.SMTPAuthenticationError(535, b'bad credentials')

    def send_message(self, message):
        self.calls.append('send')
        self.sent.append(message)


@pytest.fixture()
def smtp_app(app, monkeypatch):
    import backend.email_delivery as delivery

    FakeSMTP.instances = []
    FakeSMTP.fail_login = False
    monkeypatch.setattr(delivery.smtplib, 'SMTP', FakeSMTP)
    monkeypatch.setattr(delivery.smtplib, 'SMTP_SSL', FakeSMTP)
    app.config.update(
        TESTING=False,
        RESEND_API_KEY='',
        TRANSACTIONAL_EMAIL_FROM='',
        SMTP_HOST='smtp.gmail.com',
        SMTP_PORT=587,
        SMTP_USERNAME='thirdshot.app@gmail.com',
        SMTP_PASSWORD='abcd efgh ijkl mnop',
    )
    return app


def test_smtp_sends_text_and_html_as_the_signed_in_account(smtp_app):
    with smtp_app.app_context():
        result = send_transactional_email(
            to='Player@Example.com',
            subject='123456 is your Third Shot code',
            html='<p>123456</p>',
            text='Your code is 123456.',
            idempotency_key='email-code-abc',
        )

    assert result.provider == 'smtp' and result.captured is False
    server = FakeSMTP.instances[-1]
    assert (server.host, server.port, server.timeout) == ('smtp.gmail.com', 587, 10)
    assert server.calls == [
        'starttls',
        ('login', 'thirdshot.app@gmail.com', 'abcdefghijklmnop'),
        'send',
        'quit',
    ]
    message = server.sent[0]
    assert message['From'] == 'Third Shot <thirdshot.app@gmail.com>'
    assert message['To'] == 'player@example.com'
    assert message['Subject'] == '123456 is your Third Shot code'
    assert message['Message-ID'] == result.message_id
    assert message['Message-ID'].endswith('@third-shot.vercel.app>')
    assert message['Date']
    assert b'multipart/alternative' in message.as_bytes()
    assert message.get_body(('plain',)).get_content().strip() == 'Your code is 123456.'
    assert message.get_body(('html',)).get_content().strip() == '<p>123456</p>'


def test_smtp_uses_implicit_tls_on_port_465_and_configured_sender(smtp_app):
    smtp_app.config.update(SMTP_PORT=465, TRANSACTIONAL_EMAIL_FROM='Courts <courts@example.com>')
    with smtp_app.app_context():
        send_transactional_email(
            to='a@example.com', subject='Hi', text='Hello', idempotency_key='k-1',
        )

    server = FakeSMTP.instances[-1]
    assert server.implicit_tls is True
    assert 'starttls' not in server.calls
    assert server.sent[0]['From'] == 'Courts <courts@example.com>'


def test_smtp_rejected_login_is_a_delivery_error(smtp_app):
    from backend.email_delivery import EmailDeliveryError

    FakeSMTP.fail_login = True
    with smtp_app.app_context(), pytest.raises(EmailDeliveryError, match='rejected_auth'):
        send_transactional_email(
            to='a@example.com', subject='Hi', text='Hello', idempotency_key='k-2',
        )
    assert FakeSMTP.instances[-1].sent == []


def test_delivery_is_unavailable_without_resend_or_complete_smtp(smtp_app):
    from backend.email_delivery import EmailDeliveryUnavailable, delivery_configured

    assert delivery_configured(smtp_app) is True
    smtp_app.config['SMTP_PASSWORD'] = ''
    assert delivery_configured(smtp_app) is False
    with smtp_app.app_context(), pytest.raises(EmailDeliveryUnavailable):
        send_transactional_email(
            to='a@example.com', subject='Hi', text='Hello', idempotency_key='k-3',
        )
    assert FakeSMTP.instances == []
    smtp_app.config.update(RESEND_API_KEY='re_test', TRANSACTIONAL_EMAIL_FROM='Third Shot <hi@example.com>')
    assert delivery_configured(smtp_app) is True
