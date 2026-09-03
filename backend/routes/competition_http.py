"""HTTP response helpers shared by live competition detail endpoints."""

import hashlib

from flask import jsonify, request


def conditional_competition_detail(payload, *, kind, entity_id, viewer_id):
    """Return a private, viewer-scoped JSON representation with an ETag.

    Competition detail payloads include viewer-specific permissions, pending
    actions, and unread counts.  Include the authenticated viewer in the
    validator even when two rendered payloads happen to be byte-identical so a
    browser or intermediary can never reuse one account's validator for
    another account.
    """
    response = jsonify(payload)
    identity = f'{kind}:{int(entity_id)}:viewer:{int(viewer_id)}:'.encode()
    digest = hashlib.sha256(identity + response.get_data()).hexdigest()
    response.set_etag(digest)
    response.headers['Cache-Control'] = 'private, no-cache'
    response.vary.add('Authorization')
    return response.make_conditional(request)
