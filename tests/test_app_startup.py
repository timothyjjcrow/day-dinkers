"""Tests for app startup helpers and production origin handling."""

from backend.app import _canonical_origin, _derive_production_allowed_origins


def _clear_origin_env(monkeypatch):
    for key in (
        'CORS_ALLOWED_ORIGINS',
        'PUBLIC_APP_URL',
        'FRONTEND_URL',
        'RENDER_EXTERNAL_URL',
        'RENDER_EXTERNAL_HOSTNAME',
    ):
        monkeypatch.delenv(key, raising=False)


def test_canonical_origin_normalizes_and_rejects_invalid_values():
    assert _canonical_origin('https://app.example.com/path?q=1') == 'https://app.example.com'
    assert _canonical_origin('app.example.com') == 'https://app.example.com'
    assert _canonical_origin('http://localhost:3000/home') == 'http://localhost:3000'
    assert _canonical_origin('*') == ''
    assert _canonical_origin('javascript:alert(1)') == ''


def test_derive_production_allowed_origins_uses_render_external_url(monkeypatch):
    _clear_origin_env(monkeypatch)
    monkeypatch.setenv('RENDER_EXTERNAL_URL', 'https://my-service.onrender.com')
    assert _derive_production_allowed_origins() == ['https://my-service.onrender.com']


def test_derive_production_allowed_origins_dedupes_and_merges_env_sources(monkeypatch):
    _clear_origin_env(monkeypatch)
    monkeypatch.setenv('PUBLIC_APP_URL', 'https://app.example.com')
    monkeypatch.setenv('FRONTEND_URL', 'https://app.example.com/')
    monkeypatch.setenv('CORS_ALLOWED_ORIGINS', 'https://api.example.com, https://app.example.com')
    monkeypatch.setenv('RENDER_EXTERNAL_HOSTNAME', 'my-service.onrender.com')

    origins = _derive_production_allowed_origins()
    assert origins == [
        'https://api.example.com',
        'https://app.example.com',
        'https://my-service.onrender.com',
    ]
