#!/usr/bin/env python3
"""Vercel build step for Third Shot (``[tool.vercel.scripts] build``).

Serverless instances never run DDL, so a release that adds columns must reach
the database before its code serves traffic. Vercel runs this script after
installing Python dependencies and before the deployment goes live:

* Production builds apply the idempotent additive schema upgrades with
  ``scripts/migrate_production_schema.py`` against Neon's direct (unpooled)
  connection, then verify every table, column and index the release needs.
  Any failure fails the build, so the previous deployment keeps serving.
* Preview and local builds never touch a database. They only confirm the
  migration dependencies import, so a broken build environment shows up on
  the pull request instead of on the next production deploy.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIRECT_URL_VARIABLES = ('DATABASE_URL_UNPOOLED', 'POSTGRES_URL_NON_POOLING')
# The migration gives up on a table lock after a few seconds rather than stall
# the live app behind it, so a busy moment gets a couple more tries.
MIGRATION_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 10


def main() -> int:
    import psycopg  # noqa: F401  (dependency check for the migration)
    import sqlalchemy  # noqa: F401

    vercel_env = os.getenv('VERCEL_ENV', '').strip()
    if os.getenv('VERCEL') and not vercel_env:
        # Without it a production build can't be told apart from a preview.
        print(
            'Schema migration failed: VERCEL_ENV is missing. Turn on '
            '"Automatically expose System Environment Variables" for this project.',
            file=sys.stderr,
        )
        return 1
    if vercel_env != 'production':
        print(
            'Schema migration skipped: this is not a production build '
            f'(VERCEL_ENV={vercel_env or "unset"}).'
        )
        return 0

    target_url = next(
        (os.getenv(name, '').strip() for name in DIRECT_URL_VARIABLES
         if os.getenv(name, '').strip()),
        '',
    )
    if not target_url:
        print(
            'Schema migration failed: production build has no direct database '
            'URL (' + ' or '.join(DIRECT_URL_VARIABLES) + ').',
            file=sys.stderr,
        )
        return 1

    env = dict(os.environ, TARGET_DATABASE_URL=target_url)
    for attempt in range(1, MIGRATION_ATTEMPTS + 1):
        print('Applying additive schema upgrades before this production deploy…')
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / 'scripts' / 'migrate_production_schema.py')],
            cwd=PROJECT_ROOT,
            env=env,
            check=False,
        )
        if result.returncode == 0 or attempt == MIGRATION_ATTEMPTS:
            return result.returncode
        print(f'Schema migration attempt {attempt} failed; retrying.', file=sys.stderr)
        time.sleep(RETRY_DELAY_SECONDS)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
