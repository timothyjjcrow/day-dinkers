"""SSRF-aware HTTPS link probing with redirect revalidation."""
from __future__ import annotations

from dataclasses import dataclass
import http.client
import ipaddress
import socket
import ssl
import time
from urllib.error import URLError
from urllib.parse import urljoin, urlsplit

from backend.integrations.errors import ValidationError
from backend.integrations.safety import safe_external_url


@dataclass(frozen=True)
class LinkProbeResult:
    status: str
    http_status: int | None
    latency_ms: int | None
    error_code: str
    final_url: str


def _assert_public_host(url, *, resolver=socket.getaddrinfo):
    parsed = urlsplit(safe_external_url(url, required=True))
    host = parsed.hostname
    if not host:
        raise ValidationError('link_host_required')
    if parsed.port not in (None, 443):
        raise ValidationError('link_health_requires_port_443')
    try:
        rows = resolver(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise ValidationError('link_dns_unavailable')
    if not rows:
        raise ValidationError('link_dns_unavailable')
    addresses = []
    for row in rows:
        try:
            address = ipaddress.ip_address(row[4][0])
        except ValueError:
            raise ValidationError('link_dns_invalid')
        if not address.is_global:
            raise ValidationError('private_link_target_rejected')
        if str(address) not in addresses:
            addresses.append(str(address))
    return parsed.geturl(), tuple(addresses)


def _pinned_transport(url, *, timeout, resolved_ips):
    """Connect to a validated IP while keeping the hostname for SNI/certs."""
    parsed = urlsplit(url)
    host = parsed.hostname
    request_target = parsed.path or '/'
    if parsed.query:
        request_target += f'?{parsed.query}'
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
                raise TimeoutError('link_probe_deadline_exceeded')
            raw_socket = socket.create_connection(
                (address, 443), timeout=max(0.1, remaining),
            )
            context = ssl.create_default_context()
            tls_socket = context.wrap_socket(raw_socket, server_hostname=host)
            tls_socket.settimeout(max(0.1, deadline - time.monotonic()))
            request_bytes = (
                f'HEAD {request_target} HTTP/1.1\r\n'
                f'Host: {host}\r\n'
                'User-Agent: ThirdShot-LinkHealth/1.0\r\n'
                'Accept: */*\r\n'
                'Connection: close\r\n\r\n'
            ).encode('ascii')
            tls_socket.sendall(request_bytes)
            response = http.client.HTTPResponse(tls_socket)
            response.begin()
            return (
                response.status,
                {key.lower(): value for key, value in response.getheaders()},
                int((time.monotonic() - started) * 1000),
            )
        except (OSError, ssl.SSLError, http.client.HTTPException) as error:
            last_error = error
        finally:
            if response is not None:
                response.close()
            elif tls_socket is not None:
                tls_socket.close()
            elif raw_socket is not None:
                raw_socket.close()
    raise last_error or OSError('no_validated_link_address')


def probe_https_url(url, *, timeout=5, max_redirects=3, resolver=socket.getaddrinfo,
                    transport=None):
    custom_transport = transport is not None
    transport = transport or _pinned_transport
    current = safe_external_url(url, required=True)
    total_latency = 0
    deadline = time.monotonic() + max(0.1, float(timeout))
    try:
        for _ in range(max_redirects + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('link_probe_deadline_exceeded')
            current, resolved_ips = _assert_public_host(current, resolver=resolver)
            if custom_transport:
                status, headers, latency = transport(
                    current, timeout=max(0.1, remaining),
                )
            else:
                status, headers, latency = transport(
                    current, timeout=max(0.1, remaining),
                    resolved_ips=resolved_ips,
                )
            headers = {
                str(key).lower(): value for key, value in headers.items()
            }
            total_latency += max(0, int(latency or 0))
            if 300 <= int(status) < 400:
                if not headers.get('location'):
                    return LinkProbeResult(
                        'broken', int(status), total_latency,
                        'redirect_location_required', current,
                    )
                current = safe_external_url(
                    urljoin(current, headers['location']), required=True,
                )
                continue
            if 200 <= int(status) < 300 or int(status) in {401, 403, 405}:
                return LinkProbeResult('healthy', int(status), total_latency, '', current)
            return LinkProbeResult('broken', int(status), total_latency, f'http_{status}', current)
        return LinkProbeResult('broken', None, total_latency, 'too_many_redirects', current)
    except ValidationError as error:
        return LinkProbeResult('unsafe', None, total_latency or None, error.code, '')
    except (OSError, TimeoutError, URLError):
        return LinkProbeResult('unreachable', None, total_latency or None, 'network_error', '')
