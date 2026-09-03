"""Bounded, SSRF-resistant pulls of a business-owned JSON catalog feed."""
from __future__ import annotations

from dataclasses import dataclass
import http.client
import json
import socket
import ssl
import time
from urllib.parse import quote, urljoin, urlsplit

from backend.integrations.errors import RetryableProviderError, ValidationError
from backend.integrations.link_health import _assert_public_host
from backend.integrations.safety import safe_external_url


DEFAULT_MAX_BYTES = 1_000_000


@dataclass(frozen=True)
class CatalogPullResult:
    payload: dict | None
    not_modified: bool
    etag: str
    last_modified: str
    latency_ms: int


def _safe_validator(value, maximum):
    text = str(value or '').strip()
    if len(text) > maximum or any(
        ord(character) < 32 or ord(character) == 127 for character in text
    ):
        return ''
    return text


def _request_target(parsed):
    path = quote(parsed.path or '/', safe='/%:@!$&\'()*+,;=-._~')
    if parsed.query:
        path += '?' + quote(parsed.query, safe='=&%:@!$\'()*+,;/?-._~')
    return path


def _pinned_json_transport(url, *, timeout, resolved_ips, request_headers,
                           max_bytes):
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    host_header = host.encode('idna').decode('ascii')
    target = _request_target(parsed)
    started = time.monotonic()
    deadline = started + max(0.1, float(timeout))
    last_error = None
    for address in resolved_ips:
        raw_socket = None
        tls_socket = None
        response = None
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('catalog_pull_deadline_exceeded')
            raw_socket = socket.create_connection(
                (address, 443), timeout=max(0.1, remaining),
            )
            context = ssl.create_default_context()
            tls_socket = context.wrap_socket(raw_socket, server_hostname=host)
            tls_socket.settimeout(max(0.1, deadline - time.monotonic()))
            lines = [
                f'GET {target} HTTP/1.1',
                f'Host: {host_header}',
                'User-Agent: ThirdShot-CatalogPull/1.0',
                'Accept: application/json',
                'Accept-Encoding: identity',
                'Connection: close',
            ]
            for key in ('If-None-Match', 'If-Modified-Since'):
                if request_headers.get(key):
                    lines.append(f'{key}: {request_headers[key]}')
            tls_socket.sendall(('\r\n'.join(lines) + '\r\n\r\n').encode('ascii'))
            response = http.client.HTTPResponse(tls_socket)
            response.begin()
            content_length = response.getheader('Content-Length')
            if content_length:
                try:
                    if int(content_length) > max_bytes:
                        raise ValidationError('catalog_response_too_large')
                except ValueError:
                    raise ValidationError('invalid_catalog_content_length')
            body = b'' if response.status in {304} else response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ValidationError('catalog_response_too_large')
            return (
                int(response.status),
                {key.lower(): value for key, value in response.getheaders()},
                body,
                int((time.monotonic() - started) * 1000),
            )
        except ValidationError:
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException) as error:
            last_error = error
        finally:
            if response is not None:
                response.close()
            elif tls_socket is not None:
                tls_socket.close()
            elif raw_socket is not None:
                raw_socket.close()
    raise last_error or OSError('no_validated_catalog_address')


def pull_json_catalog(url, *, etag='', last_modified='', timeout=8,
                      max_redirects=3, max_bytes=DEFAULT_MAX_BYTES,
                      resolver=socket.getaddrinfo, transport=None):
    """Fetch and decode one JSON object, revalidating every redirect target."""
    if max_bytes < 1 or max_bytes > DEFAULT_MAX_BYTES:
        raise ValidationError('invalid_catalog_response_limit')
    conditional_headers = {
        'If-None-Match': _safe_validator(etag, 500),
        'If-Modified-Since': _safe_validator(last_modified, 120),
    }
    current = safe_external_url(url, required=True)
    total_latency = 0
    deadline = time.monotonic() + max(0.1, float(timeout))
    custom_transport = transport is not None
    transport = transport or _pinned_json_transport
    try:
        for _ in range(max_redirects + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('catalog_pull_deadline_exceeded')
            current, resolved_ips = _assert_public_host(current, resolver=resolver)
            options = {
                'timeout': max(0.1, remaining),
                'request_headers': conditional_headers,
                'max_bytes': max_bytes,
            }
            if not custom_transport:
                options['resolved_ips'] = resolved_ips
            status, raw_headers, body, latency = transport(current, **options)
            total_latency += max(0, int(latency or 0))
            headers = {str(key).lower(): str(value) for key, value in raw_headers.items()}
            if 300 <= int(status) < 400 and headers.get('location'):
                current = safe_external_url(
                    urljoin(current, headers['location']), required=True,
                )
                continue
            response_etag = _safe_validator(headers.get('etag'), 500)
            response_modified = _safe_validator(headers.get('last-modified'), 120)
            if int(status) == 304:
                return CatalogPullResult(
                    payload=None,
                    not_modified=True,
                    etag=response_etag or conditional_headers['If-None-Match'],
                    last_modified=(
                        response_modified or conditional_headers['If-Modified-Since']
                    ),
                    latency_ms=total_latency,
                )
            if not 200 <= int(status) < 300:
                raise RetryableProviderError('catalog_source_unavailable')
            content_type = headers.get('content-type', '').split(';', 1)[0].strip().lower()
            if not (
                content_type == 'application/json'
                or content_type.endswith('+json')
            ):
                raise ValidationError('catalog_content_type_must_be_json')
            if len(body) > max_bytes:
                raise ValidationError('catalog_response_too_large')
            try:
                payload = json.loads(body.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValidationError('invalid_catalog_json')
            if not isinstance(payload, dict):
                raise ValidationError('catalog_must_be_an_object')
            return CatalogPullResult(
                payload=payload,
                not_modified=False,
                etag=response_etag,
                last_modified=response_modified,
                latency_ms=total_latency,
            )
        raise ValidationError('too_many_catalog_redirects')
    except ValidationError:
        raise
    except RetryableProviderError:
        raise
    except (OSError, TimeoutError):
        raise RetryableProviderError('catalog_source_unavailable')
