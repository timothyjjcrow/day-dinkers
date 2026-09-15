"""Build a valid reviewed-plan request for tests of other session behavior.

Entry-consent regressions use raw client.post calls to exercise missing/stale
snapshots. This helper never retries or suppresses a rejected write.
"""
def post_reviewed_game(client, path, **kwargs):
    payload=kwargs.get('json', {})
    if 'data' not in kwargs and isinstance(payload, dict) and payload.get('accept') is not False:
        base=path.removesuffix('/waitlist/respond').removesuffix('/join')
        viewed=client.get(base, headers=kwargs.get('headers'))
        game=viewed.get_json(silent=True) or {}
        if viewed.status_code==200 and game.get('plan_token'):
            payload=dict(payload)
            payload.setdefault('expected_plan_token',game['plan_token'])
            kwargs={**kwargs,'json':payload}
    return client.post(path, **kwargs)
