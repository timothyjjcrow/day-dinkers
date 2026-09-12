"""Opt-in release races against one disposable, explicitly named local database.

Run with APP_ENV=testing and both TEST_DATABASE_URL and DATABASE_URL set to
postgresql+psycopg://127.0.0.1:55441/thirdshot_release_concurrency.
The guard runs before importing the application or creating/dropping tables.
"""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import make_url


def _disposable_database_selected():
    if os.environ.get('APP_ENV') != 'testing':
        return False
    for key in ('TEST_DATABASE_URL', 'DATABASE_URL'):
        try:
            url = make_url(os.environ.get(key, ''))
        except Exception:
            return False
        if (url.drivername != 'postgresql+psycopg' or url.host != '127.0.0.1'
                or url.port != 55441 or url.database != 'thirdshot_release_concurrency'):
            return False
    return True


if not _disposable_database_selected():
    pytest.skip('Requires the dedicated disposable local PostgreSQL concurrency database',
                allow_module_level=True)

from backend.app import create_app, db
from backend.models import Court, Game, GamePlayer, User, utcnow
from backend.routes.auth import _issue_token
from backend.routes import games as game_routes


@pytest.fixture()
def fixture():
    app = create_app('testing')
    app.config.update(PUSH_DELIVERY_ENABLED=False)
    with app.app_context():
        assert db.engine.url.database == 'thirdshot_release_concurrency'
        db.drop_all()
        db.create_all()
        people = [User(email=f'pg-race-{index}@example.test',
                       display_name=f'Race player {index}', password_hash='unused')
                  for index in range(4)]
        court = Court(name='Synthetic concurrency court', latitude=45, longitude=-122)
        db.session.add_all([*people, court])
        db.session.commit()
        data = {
            'app': app, 'engine': db.engine, 'people': [person.id for person in people],
            'headers': [{'Authorization': f'Bearer {_issue_token(person)}'} for person in people],
            'court_id': court.id,
            'start': utcnow().replace(second=0, microsecond=0) + timedelta(days=2),
        }
        db.session.remove()
    yield data
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def _seed_game(data, owner, start, *, visibility='open'):
    with data['app'].app_context():
        game = Game(creator_id=data['people'][owner], court_id=data['court_id'],
                    scheduled_at=start, duration_minutes=60, game_type='casual',
                    title='Synthetic dated commitment', visibility=visibility, max_players=4)
        db.session.add(game)
        db.session.flush()
        db.session.add(GamePlayer(game_id=game.id, user_id=data['people'][owner]))
        db.session.commit()
        return game.id


def _create_payload(data, attempt, **extra):
    return {'court_id': data['court_id'], 'scheduled_at': data['start'].isoformat(),
            'duration_minutes': 60, 'game_type': 'casual', 'max_players': 4,
            'visibility': 'open', 'client_attempt_id': attempt, **extra}


def _request(data, method, path, payload, actor=0):
    # Each thread gets a separate request/app context and scoped DB session.
    with data['app'].test_client() as client:
        response = client.open(path, method=method, json=payload, headers=data['headers'][actor])
        return response.status_code, response.get_json()


def _backend_pid(connection):
    return connection.connection.driver_connection.info.backend_pid


def _wait_for_postgres_lock(engine, pid, *, timeout=8):
    deadline = time.monotonic() + timeout
    with engine.connect() as observer:
        while time.monotonic() < deadline:
            row = observer.execute(text(
                'SELECT state, wait_event_type, wait_event FROM pg_stat_activity WHERE pid=:pid'
            ), {'pid': pid}).mappings().one()
            # pg_stat_activity snapshots otherwise remain cached in a transaction.
            observer.commit()
            if row['wait_event_type'] == 'Lock':
                return dict(row)
            time.sleep(0.01)
    raise AssertionError(f'PostgreSQL backend {pid} never waited on a real row/transaction lock')


@pytest.mark.parametrize('operations', [('create', 'create'), ('join', 'join'), ('create', 'join')])
def test_same_player_overlapping_commitments_serialize_before_review(fixture, operations):
    data = fixture
    requests = []
    for index, operation in enumerate(operations):
        if operation == 'create':
            requests.append(('POST', '/api/games', _create_payload(data, f'pg-create-race-{index}')))
        else:
            target = _seed_game(data, index + 1, data['start'])
            requests.append(('POST', f'/api/games/{target}/join', {}))
    barrier = threading.Barrier(2)
    first_locked = threading.Event()
    release = threading.Event()
    pids = {}
    winner = []

    def before(connection, cursor, statement, parameters, context, many):
        name = threading.current_thread().name
        if (name.startswith('pg-commit') and name not in pids
                and 'FROM "user"' in statement and 'FOR UPDATE' in statement):
            pids[name] = _backend_pid(connection)
            barrier.wait(timeout=10)

    def after(connection, cursor, statement, parameters, context, many):
        if (threading.current_thread().name.startswith('pg-commit')
                and 'FROM "user"' in statement and 'FOR UPDATE' in statement
                and not first_locked.is_set()):
            winner.append(_backend_pid(connection))
            first_locked.set()
            assert release.wait(timeout=12), 'Coordinator did not release the first transaction'

    engine = data['engine']
    event.listen(engine, 'before_cursor_execute', before)
    event.listen(engine, 'after_cursor_execute', after)
    try:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix='pg-commit') as pool:
            futures = [pool.submit(_request, data, *request) for request in requests]
            try:
                assert first_locked.wait(timeout=10)
                assert len(set(pids.values())) == 2
                waiting_pid = next(pid for pid in pids.values() if pid != winner[0])
                lock = _wait_for_postgres_lock(engine, waiting_pid)
            finally:
                release.set()
            results = [future.result(timeout=15) for future in futures]
    finally:
        release.set()
        event.remove(engine, 'before_cursor_execute', before)
        event.remove(engine, 'after_cursor_execute', after)
    assert sum(status in (200, 201) for status, _ in results) == 1, results
    warnings = [body for status, body in results if status == 409]
    assert len(warnings) == 1 and warnings[0]['error'] == 'schedule_conflict', results
    assert warnings[0]['schedule_conflict_token'] and warnings[0]['conflicts']
    with data['app'].app_context():
        memberships = GamePlayer.query.filter_by(user_id=data['people'][0]).all()
        assert len(memberships) == 1
        committed_id = memberships[0].game_id
    print(f'{operations}: independent PostgreSQL PIDs {sorted(pids.values())}; '
          f'wait={lock}; HTTP={[status for status, _ in results]}; committed_game={committed_id}')


def test_following_date_edit_rechecks_roster_after_concurrent_join(fixture, monkeypatch):
    data = fixture
    status, created = _request(data, 'POST', '/api/games', _create_payload(
        data, 'pg-series-race', recurrence='weekly', recurrence_timezone='UTC'))
    assert status == 201, created
    root_id = created['id']
    with data['app'].app_context():
        following = Game.query.filter(Game.recurrence_series_id == root_id, Game.id != root_id
                                      ).order_by(Game.scheduled_at).first()
        assert following is not None
        following_id, original = following.id, following.scheduled_at
        original_dates = {row.id: row.scheduled_at for row in Game.query.filter_by(
            recurrence_series_id=root_id).all()}
    # Joining the original date is conflict-free; moving the series by 90 minutes is not.
    _seed_game(data, 1, original + timedelta(minutes=90), visibility='private')
    join_locked = threading.Event()
    edit_attempted_lock = threading.Event()
    release_join = threading.Event()
    pids = {}
    snapshots = []
    locked_rosters = []
    rollbacks = []
    original_snapshot = game_routes._game_edit_lock_snapshot

    def snapshot(*args, **kwargs):
        result = original_snapshot(*args, **kwargs)
        if threading.current_thread().name.startswith('pg-edit'):
            snapshots.append(set(result[1]))
        return result

    monkeypatch.setattr(game_routes, '_game_edit_lock_snapshot', snapshot)

    def before(connection, cursor, statement, parameters, context, many):
        name = threading.current_thread().name
        if name.startswith('pg-edit') and 'FROM "user"' in statement and 'FOR NO KEY UPDATE' in statement:
            locked_rosters.append(set(parameters.values()))
        if name.startswith('pg-edit') and 'FROM game' in statement and 'FOR UPDATE' in statement:
            # A closure rollback may return its connection to the pool. Retain
            # the initial backend that demonstrably waited on the joiner.
            pids.setdefault('edit', _backend_pid(connection))
            edit_attempted_lock.set()

    def after(connection, cursor, statement, parameters, context, many):
        name = threading.current_thread().name
        if (name.startswith('pg-join') and not join_locked.is_set()
                and 'FROM game' in statement and 'FOR UPDATE' in statement):
            pids['join'] = _backend_pid(connection)
            join_locked.set()
            assert release_join.wait(timeout=12)

    def rollback(connection):
        if threading.current_thread().name.startswith('pg-edit'):
            rollbacks.append(_backend_pid(connection))

    engine = data['engine']
    event.listen(engine, 'before_cursor_execute', before)
    event.listen(engine, 'after_cursor_execute', after)
    event.listen(engine, 'rollback', rollback)
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='pg-join') as joins, \
                ThreadPoolExecutor(max_workers=1, thread_name_prefix='pg-edit') as edits:
            joined = joins.submit(_request, data, 'POST', f'/api/games/{following_id}/join',
                                  {'standing_rsvp': False}, 1)
            try:
                assert join_locked.wait(timeout=10)
                edited = edits.submit(_request, data, 'PATCH', f'/api/games/{root_id}', {
                    'scheduled_at': (data['start'] + timedelta(minutes=90)).isoformat(),
                    'edit_scope': 'following_dates',
                })
                assert edit_attempted_lock.wait(timeout=10)
                assert len(set(pids.values())) == 2
                lock = _wait_for_postgres_lock(engine, pids['edit'])
                assert snapshots and data['people'][1] not in snapshots[0]
            finally:
                release_join.set()
            joined_result = joined.result(timeout=15)
            edited_result = edited.result(timeout=15)
    finally:
        release_join.set()
        event.remove(engine, 'before_cursor_execute', before)
        event.remove(engine, 'after_cursor_execute', after)
        event.remove(engine, 'rollback', rollback)
    assert joined_result[0] == 200, joined_result
    assert edited_result[0] == 409, edited_result
    assert edited_result[1]['error'] == 'schedule_conflict'
    assert any(row['user_id'] == data['people'][1] and row['kind'] == 'busy'
               for row in edited_result[1]['conflicts'])
    assert any(data['people'][1] in roster for roster in snapshots[1:])
    assert len(locked_rosters) >= 2 and data['people'][1] not in locked_rosters[0]
    assert data['people'][1] in locked_rosters[1]
    assert len(rollbacks) >= 2  # Closure restart, then the refused edit transaction.
    with data['app'].app_context():
        assert {row.id: row.scheduled_at for row in Game.query.filter_by(
            recurrence_series_id=root_id).all()} == original_dates
        assert GamePlayer.query.filter_by(game_id=following_id, user_id=data['people'][1]).count() == 1
    accepted = _request(data, 'PATCH', f'/api/games/{root_id}', {
        'scheduled_at': (data['start'] + timedelta(minutes=90)).isoformat(),
        'edit_scope': 'following_dates',
        'schedule_conflict_ack': edited_result[1]['schedule_conflict_token'],
    })
    assert accepted[0] == 200, accepted
    with data['app'].app_context():
        assert all(db.session.get(Game, game_id).scheduled_at == start + timedelta(minutes=90)
                   for game_id, start in original_dates.items())
        assert GamePlayer.query.filter_by(game_id=following_id, user_id=data['people'][1]).count() == 1
    print(f'following-date closure: PostgreSQL PIDs={pids}; wait={lock}; '
          f'roster snapshots={snapshots}; user locks={locked_rosters}; rollbacks={len(rollbacks)}; '
          f'HTTP={[joined_result[0], edited_result[0], accepted[0]]}')


def test_confirmation_waits_for_price_edit_then_rejects_stale_review(fixture, monkeypatch):
    data=fixture
    game_id=_seed_game(data,0,data['start'])
    with data['app'].app_context():
        game=db.session.get(Game,game_id)
        game.cost_cents=0
        db.session.add(GamePlayer(game_id=game_id,user_id=data['people'][1],attending_at=utcnow()))
        db.session.commit()
    assert _request(data,'PATCH',f'/api/games/{game_id}',{'cost_cents':500},actor=0)[0]==200
    _,detail=_request(data,'GET',f'/api/games/{game_id}',None,actor=1)
    expected=detail['my_commitment_requested_at']
    edit_locked=threading.Event()
    confirmation_started=threading.Event()
    release=threading.Event()
    pids={}
    original=game_routes._lock_stable_game_edit_scope

    def pause_edit(game_id,actor_id,**kwargs):
        result=original(game_id,actor_id,**kwargs)
        if threading.current_thread().name.startswith('pg-reconfirm') and actor_id==data['people'][0]:
            edit_locked.set()
            assert release.wait(timeout=15)
        return result

    def before(connection,cursor,statement,parameters,context,many):
        if (threading.current_thread().name=='pg-reconfirm_1' and 'FROM "user"' in statement
                and 'FOR NO KEY UPDATE' in statement and not confirmation_started.is_set()):
            pids['confirm']=_backend_pid(connection)
            confirmation_started.set()

    monkeypatch.setattr(game_routes,'_lock_stable_game_edit_scope',pause_edit)
    event.listen(data['engine'],'before_cursor_execute',before)
    try:
        with ThreadPoolExecutor(max_workers=2,thread_name_prefix='pg-reconfirm') as pool:
            edit=pool.submit(_request,data,'PATCH',f'/api/games/{game_id}',{'cost_cents':1000},0)
            assert edit_locked.wait(timeout=10)
            confirm=pool.submit(_request,data,'POST',f'/api/games/{game_id}/attend',
                {'expected_commitment_requested_at':expected},1)
            try:
                assert confirmation_started.wait(timeout=10)
                _wait_for_postgres_lock(data['engine'],pids['confirm'])
            finally:
                release.set()
            assert edit.result(timeout=15)[0]==200
            status,body=confirm.result(timeout=15)
    finally:
        release.set()
        event.remove(data['engine'],'before_cursor_execute',before)
    assert status==409 and body['error']=='game_commitment_changed'
    assert body['game']['cost_cents']==1000 and body['game']['commitment_confirmation_due']
    with data['app'].app_context():
        player=GamePlayer.query.filter_by(game_id=game_id,user_id=data['people'][1]).one()
        assert player.attending_at is None and player.commitment_requested_at is not None
