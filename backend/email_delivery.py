"""Small, provider-isolated transactional email delivery layer.

The application runs on Flask, so using Resend's HTTP API directly keeps the
dependency surface small while preserving the same server-side security and
idempotency guarantees as its SDK.  Tests never touch the network: messages are
captured in ``app.extensions['email_outbox']`` instead.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
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
        'from': str(current_app.config.get('TRANSACTIONAL_EMAIL_FROM') or '').strip(),
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

    api_key = str(current_app.config.get('RESEND_API_KEY') or '').strip()
    if not api_key or not payload['from']:
        raise EmailDeliveryUnavailable('transactional_email_not_configured')

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
