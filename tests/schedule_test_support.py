"""Explicitly accept conflicts in fixtures that intentionally create overlapping plans."""

def post_with_schedule_review(client, path, **kwargs):
    response = client.post(path, **kwargs)
    payload = response.get_json(silent=True) or {}
    if response.status_code == 409 and payload.get('error') == 'schedule_conflict':
        kwargs = {**kwargs, 'json': {**(kwargs.get('json') or {}),
            'schedule_conflict_ack': payload['schedule_conflict_token']}}
        response = client.post(path, **kwargs)
    return response
