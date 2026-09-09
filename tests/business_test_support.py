"""Make ordinary manager test actions with the same precondition as the UI.

Concurrency tests deliberately use the raw client to exercise absent/stale
versions. This helper only fetches a version before an ordinary single edit.
"""
import re


def venue_write(client, method, path, **kwargs):
    headers = dict(kwargs.get('headers') or {})
    match = re.fullmatch(r'/api/businesses/(\d+)(?:/(?:schedule|offerings|logo|revisions/\d+/restore))?', path)
    if match and 'If-Match' not in headers:
        current = client.get(f'/api/businesses/{match[1]}', headers=headers)
        data = current.get_json() or {}
        if data.get('content_version'):
            headers['If-Match'] = '"' + data['content_version'] + '"'
        kwargs['headers'] = headers
    return getattr(client, method)(path, **kwargs)
