"""Small, provider-isolated transactional email delivery layer.

The application runs on Flask, so using Resend's HTTP API directly keeps the
dependency surface small while preserving the same server-side security and
idempotency guarantees as its SDK.  When Resend is not configured, plain SMTP
(for example a Gmail app password) sends the same messages.  Tests never touch
the network: messages are captured in ``app.extensions['email_outbox']``
instead.
"""
from __future__ import annotations

import json
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from flask import current_app


_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


class EmailDeliveryUnavailable(RuntimeError):
    """Email delivery is intentionally disabled until production is configured."""


class EmailDeliveryError(RuntimeError):
    """The provider rejected or failed to accept a transactional message."""


@dataclass(frozen=True)
class EmailDeliveryResult:
    message_id: str
    provider: str
    captured: bool = False


def _resend_configured(config):
    return bool(
        str(config.get('RESEND_API_KEY') or '').strip()
        and str(config.get('TRANSACTIONAL_EMAIL_FROM') or '').strip()
    )


def _smtp_configured(config):
    return bool(
        str(config.get('SMTP_HOST') or '').strip()
        and str(config.get('SMTP_USERNAME') or '').strip()
        and str(config.get('SMTP_PASSWORD') or '')
    )


def _sender(config):
    sender = str(config.get('TRANSACTIONAL_EMAIL_FROM') or '').strip()
    if not sender and _smtp_configured(config):
        # SMTP providers such as Gmail only send as the signed-in account.
        sender = f"Third Shot <{str(config.get('SMTP_USERNAME')).strip()}>"
    return sender


def delivery_configured(app=None):
    """True when a real provider can send mail with the current settings."""
    config = (app or current_app).config
    return _resend_configured(config) or _smtp_configured(config)


def _clean_header(value, *, field, maximum):
    cleaned = str(value or '').strip()
    if not cleaned or '\r' in cleaned or '\n' in cleaned:
        raise ValueError(f'invalid_{field}')
    if len(cleaned) > maximum:
        raise ValueError(f'{field}_too_long')
    return cleaned


def send_transactional_email(
    *,
    to,
    subject,
    html='',
    text='',
    idempotency_key,
):
    """Send one transactional email without exposing credentials or content.

    ``idempotency_key`` must identify the business event (invitation, ownership
    challenge, and so on), not the HTTP attempt.  That keeps retries from
    delivering duplicate messages.
    """
    recipient = _clean_header(to, field='recipient', maximum=255).lower()
    if not _EMAIL_RE.fullmatch(recipient):
        raise ValueError('invalid_recipient')
    subject = _clean_header(subject, field='subject', maximum=200)
    idempotency_key = _clean_header(
        idempotency_key, field='idempotency_key', maximum=200,
    )
    html = str(html or '').strip()
    text = str(text or '').strip()
    if not html and not text:
        raise ValueError('email_body_required')

    payload = {
        'from': _sender(current_app.config),
        'to': [recipient],
        'subject': subject,
    }
    if html:
        payload['html'] = html
    if text:
        payload['text'] = text

    if current_app.config.get('TESTING'):
        outbox = current_app.extensions.setdefault('email_outbox', [])
        message_id = f'test-email-{len(outbox) + 1}'
        outbox.append({
            'id': message_id,
            'idempotency_key': idempotency_key,
            **payload,
        })
        return EmailDeliveryResult(
            message_id=message_id,
            provider='test_outbox',
            captured=True,
        )

    config = current_app.config
    if not _resend_configured(config):
        if _smtp_configured(config):
            return _send_smtp(config, payload)
        raise EmailDeliveryUnavailable('transactional_email_not_configured')
    api_key = str(config.get('RESEND_API_KEY') or '').strip()

    body = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    req = Request(
        'https://api.resend.com/emails',
        data=body,
        method='POST',
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'Idempotency-Key': idempotency_key,
            'User-Agent': 'ThirdShot/1.0',
        },
    )
    try:
        with urlopen(req, timeout=10) as response:  # noqa: S310 - fixed HTTPS host
            response_payload = json.loads(response.read().decode('utf-8'))
    except HTTPError as exc:
        # Provider bodies can echo addresses or template data.  Keep them out
        # of application errors and logs while retaining an actionable status.
        raise EmailDeliveryError(f'email_provider_rejected_{exc.code}') from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise EmailDeliveryError('email_provider_unavailable') from exc

    message_id = str(response_payload.get('id') or '').strip()
    if not message_id:
        raise EmailDeliveryError('email_provider_invalid_response')
    return EmailDeliveryResult(message_id=message_id, provider='resend')


def _send_smtp(config, payload):
    """Send one message over SMTP with STARTTLS (or implicit TLS on 465).

    SMTP has no idempotency keys, so a retried business event can arrive
    twice; callers already treat delivery as best effort.
    """
    host = str(config.get('SMTP_HOST')).strip()
    try:
        port = int(config.get('SMTP_PORT') or 587)
    except (TypeError, ValueError):
        port = 587
    message = EmailMessage()
    try:
        message['From'] = payload['from']
        message['To'] = payload['to'][0]
        message['Subject'] = payload['subject']
    except ValueError as exc:
        raise EmailDeliveryError('email_provider_invalid_message') from exc
    domain = (
        urlparse(str(config.get('PUBLIC_APP_URL') or '')).hostname
        or parseaddr(payload['from'])[1].rpartition('@')[2]
        or None
    )
    message_id = make_msgid(domain=domain)
    message['Message-ID'] = message_id
    message['Date'] = formatdate(usegmt=True)
    if payload.get('text'):
        message.set_content(payload['text'])
        if payload.get('html'):
            message.add_alternative(payload['html'], subtype='html')
    else:
        message.set_content(payload['html'], subtype='html')

    context = ssl.create_default_context()
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=10, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=10)
        with server:
            if port != 465:
                server.starttls(context=context)
            password = str(config.get('SMTP_PASSWORD'))
            if host.endswith(('gmail.com', 'googlemail.com')):
                # Google shows app passwords in groups of four; the spaces
                # are not part of the password.
                password = password.replace(' ', '')
            server.login(str(config.get('SMTP_USERNAME')).strip(), password)
            server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailDeliveryError('email_provider_rejected_auth') from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise EmailDeliveryError('email_provider_rejected_recipient') from exc
    except (smtplib.SMTPException, OSError, TimeoutError) as exc:
        raise EmailDeliveryError('email_provider_unavailable') from exc
    return EmailDeliveryResult(message_id=message_id, provider='smtp')
