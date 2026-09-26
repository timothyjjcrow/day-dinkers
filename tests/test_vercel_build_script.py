"""The Vercel build step migrates production only, and never previews."""

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'vercel_build.py'


def load_build_script():
    spec = importlib.util.spec_from_file_location('vercel_build', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pyproject_registers_the_vercel_build_script():
    config = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    assert config['tool']['vercel']['scripts']['build'] == 'python scripts/vercel_build.py'


def test_preview_builds_never_touch_a_database(monkeypatch, capsys):
    build = load_build_script()
    monkeypatch.setenv('VERCEL_ENV', 'preview')
    monkeypatch.setenv('DATABASE_URL_UNPOOLED', 'postgresql://user:pw@ep-direct.neon.tech/db')
    calls = []
    monkeypatch.setattr(build.subprocess, 'run', lambda *a, **k: calls.append((a, k)))

    assert build.main() == 0
    assert calls == []
    assert 'not a production build' in capsys.readouterr().out


def test_production_build_without_direct_url_fails(monkeypatch):
    build = load_build_script()
    monkeypatch.setenv('VERCEL_ENV', 'production')
    for name in build.DIRECT_URL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    calls = []
    monkeypatch.setattr(build.subprocess, 'run', lambda *a, **k: calls.append((a, k)))

    assert build.main() == 1
    assert calls == []


def test_production_build_runs_the_verified_migration(monkeypatch):
    build = load_build_script()
    monkeypatch.setenv('VERCEL_ENV', 'production')
    monkeypatch.setenv('DATABASE_URL_UNPOOLED', 'postgresql://user:pw@ep-direct.neon.tech/db')
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(build.subprocess, 'run', fake_run)

    assert build.main() == 0
    (args, kwargs), = calls
    assert args == [sys.executable, str(ROOT / 'scripts' / 'migrate_production_schema.py')]
    assert kwargs['env']['TARGET_DATABASE_URL'] == 'postgresql://user:pw@ep-direct.neon.tech/db'
    assert kwargs['cwd'] == ROOT


def test_failed_migration_fails_the_build_after_retries(monkeypatch):
    build = load_build_script()
    monkeypatch.setenv('VERCEL_ENV', 'production')
    monkeypatch.setenv('DATABASE_URL_UNPOOLED', 'postgresql://user:pw@ep-direct.neon.tech/db')
    calls, sleeps = [], []
    monkeypatch.setattr(build.time, 'sleep', sleeps.append)
    monkeypatch.setattr(
        build.subprocess, 'run',
        lambda args, **kwargs: calls.append(args) or subprocess.CompletedProcess(args, 1),
    )

    assert build.main() == 1
    assert len(calls) == build.MIGRATION_ATTEMPTS
    assert sleeps == [build.RETRY_DELAY_SECONDS] * (build.MIGRATION_ATTEMPTS - 1)


def test_a_busy_moment_is_retried(monkeypatch):
    build = load_build_script()
    monkeypatch.setenv('VERCEL_ENV', 'production')
    monkeypatch.setenv('DATABASE_URL_UNPOOLED', 'postgresql://user:pw@ep-direct.neon.tech/db')
    results = [1, 0]
    monkeypatch.setattr(build.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(
        build.subprocess, 'run',
        lambda args, **kwargs: subprocess.CompletedProcess(args, results.pop(0)),
    )

    assert build.main() == 0
    assert results == []


def test_a_vercel_build_without_vercel_env_fails(monkeypatch):
    build = load_build_script()
    monkeypatch.setenv('VERCEL', '1')
    monkeypatch.delenv('VERCEL_ENV', raising=False)
    monkeypatch.setenv('DATABASE_URL_UNPOOLED', 'postgresql://user:pw@ep-direct.neon.tech/db')
    calls = []
    monkeypatch.setattr(build.subprocess, 'run', lambda *a, **k: calls.append((a, k)))

    assert build.main() == 1
    assert calls == []


def test_vercel_uploads_the_build_scripts_and_no_other_helpers():
    def ignored(path):
        result = subprocess.run(
            ['git', '-c', 'core.excludesFile=.vercelignore', 'check-ignore', '--no-index', '-q', path],
            cwd=ROOT,
        )
        return result.returncode == 0

    for needed in ('scripts/vercel_build.py', 'scripts/migrate_production_schema.py',
                   'scripts/migrate_business_integration_foundation.py', 'backend/app.py',
                   'pyproject.toml', 'app.py'):
        assert not ignored(needed), needed
    for private in ('scripts/migrate_sqlite_recovery.py', 'scripts/manage_business_operators.py',
                    'tests/test_api.py', 'instance/app.db', '.env'):
        assert ignored(private), private


def test_the_migration_gives_up_on_a_busy_table_quickly():
    source = (ROOT / 'scripts' / 'migrate_production_schema.py').read_text()
    assert "LOCK_TIMEOUT = '5s'" in source
    assert "cursor.execute(f\"SET lock_timeout = '{LOCK_TIMEOUT}'\")" in source
    assert source.index('    _limit_lock_waits()') < source.index('    from backend.app import app, db')
