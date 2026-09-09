# PostgreSQL schedule concurrency release verification

Local release verification, 2026-09-08. No production requests or deployment.

The tests use a separate synthetic PostgreSQL 14.17 database on `127.0.0.1:55441`,
named `thirdshot_release_concurrency`. They refuse to import the application or
create/drop tables unless both database URLs point to this exact disposable
database and `APP_ENV=testing`. The database uses UTF8 (`createdb -T template0 -E
UTF8 --locale=C`); the cluster's default template encoding was SQL_ASCII.

## Observed defect and bounded fix

The initial run produced **3 passed, 1 failed**. A joining player held the recurring
series Game row. The editor held the host User row with `FOR UPDATE` and waited for
that series row. The join transaction then inserted its host notification, whose
User foreign key needed a key-share lock on the host. PostgreSQL detected the
cycle and aborted the edit transaction. The server log confirms the opposing
queries were `SELECT game ... FOR UPDATE` and `INSERT INTO notification`.

Only the User closure lock in `backend/routes/games.py::_lock_stable_game_edit_scope`
was changed: `with_for_update(key_share=True)` emits PostgreSQL **FOR NO KEY UPDATE**.
It continues to serialize changes to the same player's schedule while permitting
foreign-key checks. Game, queue, and dated roster locks were retained. The existing
closure reread and rollback/retry then includes a player who joined while the edit
waited. No model or migration change was required.

## Verified outcomes

`tests/test_postgres_schedule_concurrency.py` starts independent Flask request
threads and PostgreSQL connections. The coordinator observes a real
`pg_stat_activity.wait_event_type = Lock` before releasing the winning transaction;
these are not sequential requests or SQLite lock simulations.

| Concurrent operations | Final outcome |
| --- | --- |
| Same player creates two overlapping sessions | One 201; one 409 with a fresh conflict token; exactly one roster commitment |
| Same player joins two overlapping sessions | One 200; one 409 with a fresh conflict token; exactly one roster commitment |
| Same player creates one session and joins another overlapping session | One accepted commitment; one 409 with conflict review |
| New player joins a following date while host edits the series | Join 200; edit detects new participant, rolls back and reacquires the complete User closure, then returns 409; every original date and joined roster remain intact |

The final case also retries the edit with the returned acknowledgement. Only that
explicit review changes the dates, with the joined player preserved.

Final run: **4 passed in 5.36s**. Independent backend pairs were 53143/53146,
53148/53156, 53180/53184, and 53186/53187. All four waits were PostgreSQL transaction
locks. The series edit's initial roster was `{1}`, its refreshed roster was
`{1, 2}`, and its User locks were reacquired for `{1, 2}` after rollback.

Ordinary isolated schedule regression: **10 passed, 1 skipped in 2.74s**.
The skip is intentional: the PostgreSQL module does not run against SQLite.
`git diff --check` passed for the changed source and test.

Commands:

```sh
APP_ENV=testing TEST_DATABASE_URL=postgresql+psycopg://127.0.0.1:55441/thirdshot_release_concurrency DATABASE_URL=postgresql+psycopg://127.0.0.1:55441/thirdshot_release_concurrency python3 -m pytest tests/test_postgres_schedule_concurrency.py -q -s
APP_ENV=testing TEST_DATABASE_URL=sqlite:///:memory: DATABASE_URL=sqlite:///:memory: python3 -m pytest tests/test_player_schedule_conflicts.py tests/test_postgres_schedule_concurrency.py -q
```

Detailed local logs are `tmp/product-audit-release/postgres-schedule-concurrency.log`
and `tmp/product-audit-release/sqlite-schedule-after-pg-fix.log`. The original
deadlock remains recorded in `tmp/product-audit-release/postgres.log`.

This verifies the four requested transaction boundaries, not a general load test
or proof that every application operation is free of deadlocks. Production schema,
packaged release, and deployment verification remain owned by the release task.
