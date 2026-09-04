"""Production asset build, compression, and immutable delivery contracts."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from backend.app import create_app


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / 'public'
RELEASE = PUBLIC / 'assets' / 'r59'
CI_WORKFLOW = (ROOT / '.github' / 'workflows' / 'backend-ci.yml').read_text()


def test_r59_manifest_matches_readable_sources_and_reduces_transfer_size():
    manifest = json.loads((RELEASE / 'manifest.json').read_text())
    assert manifest['release'] == 'r59'
    for output_name, metadata in manifest['files'].items():
        source = (PUBLIC / metadata['source']).read_bytes()
        output = (RELEASE / output_name).read_bytes()
        gzip_body = (RELEASE / f'{output_name}.gz').read_bytes()
        brotli_body = (RELEASE / f'{output_name}.br').read_bytes()
        assert hashlib.sha256(source).hexdigest() == metadata['source_sha256']
        assert len(source) == metadata['source_bytes']
        assert len(output) == metadata['minified_bytes']
        assert len(gzip_body) == metadata['gzip_bytes']
        assert len(brotli_body) == metadata['brotli_bytes']
        assert gzip_body[9] == 255
        assert gzip.decompress(gzip_body) == output
        assert len(output) < len(source)
        assert len(brotli_body) < len(output)
        source_map = json.loads(
            (RELEASE / f'{output_name}.map').read_text()
        )
        source_index = next(
            index for index, mapped_name in enumerate(source_map['sources'])
            if Path(mapped_name).name == metadata['source']
        )
        assert source_map['sourcesContent'][source_index] == source.decode()

    # The initial application transfer meets the audit target on Brotli-capable
    # production browsers even though the readable source remains modularized
    # only at the feature-function level for now.
    assert manifest['files']['app-v15.min.js']['brotli_bytes'] < 250 * 1024


def test_release_route_negotiates_precompressed_immutable_assets():
    app = create_app('testing')
    client = app.test_client()
    plain = client.get('/assets/r59/app-v15.min.js')
    gzip_response = client.get(
        '/assets/r59/app-v15.min.js', headers={'Accept-Encoding': 'gzip'},
    )
    brotli_response = client.get(
        '/release-assets/r59/app-v15.min.js',
        headers={'Accept-Encoding': 'br, gzip;q=0.8'},
    )
    brotli_refused = client.get(
        '/release-assets/r59/app-v15.min.js',
        headers={'Accept-Encoding': 'gzip, br;q=0'},
    )

    assert (
        plain.status_code
        == gzip_response.status_code
        == brotli_response.status_code
        == brotli_refused.status_code
        == 200
    )
    assert plain.mimetype in {'application/javascript', 'text/javascript'}
    assert plain.headers.get('Content-Encoding') is None
    assert gzip_response.headers['Content-Encoding'] == 'gzip'
    assert brotli_response.headers['Content-Encoding'] == 'br'
    assert brotli_refused.headers['Content-Encoding'] == 'gzip'
    assert gzip.decompress(gzip_response.data) == plain.data
    assert brotli_response.data == (RELEASE / 'app-v15.min.js.br').read_bytes()
    for response in (plain, gzip_response, brotli_response, brotli_refused):
        assert response.headers['Cache-Control'] == 'public, max-age=31536000, immutable'
        assert response.headers['Vary'] == 'Accept-Encoding'

    assert client.get('/assets/r57/app-v15.min.js').status_code == 404
    assert client.get('/assets/r59/not-generated.js').status_code == 404
    assert client.get('/release-assets/r57/app-v15.min.js').status_code == 404
    assert client.get('/release-assets/r59/app-v15.min.js.map').status_code == 404
    # r58 remains available to an already-open service-worker client while
    # the document moves it to the new immutable r59 URLs.
    assert client.get('/release-assets/r58/app-v15.min.js').status_code == 200


def test_vercel_static_delivery_preserves_immutable_release_caching():
    """Production serves ``public`` before Flask, so mirror Flask headers."""
    config = json.loads((ROOT / 'vercel.json').read_text())
    security_rule = next(
        rule for rule in config.get('headers', [])
        if rule.get('source') == '/(.*)'
    )
    security_headers = {
        item['key'].lower(): item['value']
        for item in security_rule.get('headers', [])
    }
    asset_rule = next(
        rule for rule in config.get('headers', [])
        if rule.get('source') == '/assets/(.*)'
    )
    headers = {
        item['key'].lower(): item['value']
        for item in asset_rule.get('headers', [])
    }
    assert headers['cache-control'] == (
        'public, max-age=31536000, immutable'
    )
    assert headers['vary'] == 'Accept-Encoding'
    assert security_headers == {
        'x-content-type-options': 'nosniff',
        'x-frame-options': 'SAMEORIGIN',
        'referrer-policy': 'strict-origin-when-cross-origin',
    }
    # The catch-all must not replace the release asset's stronger cache rule;
    # Vercel applies both matching header blocks.
    assert 'cache-control' not in security_headers


def test_vercel_negotiates_committed_brotli_release_assets():
    """Brotli-capable production clients receive our quality-11 artifacts."""
    config = json.loads((ROOT / 'vercel.json').read_text())
    expected = {
        'app-v15.min.js': 'application/javascript; charset=utf-8',
        'crew-planner-v15.min.js': 'application/javascript; charset=utf-8',
        'styles-v15.min.css': 'text/css; charset=utf-8',
    }
    rewrites = config.get('rewrites', [])
    header_rules = config.get('headers', [])
    accepted_brotli = (
        r'(^|.*,\s*)[bB][rR](\s*;\s*[qQ]\s*=\s*'
        r'(1(\.0{0,3})?|0\.([1-9][0-9]{0,2}|0[1-9][0-9]?|00[1-9])))?'
        r'\s*(,.*|$)'
    )

    for filename, content_type in expected.items():
        source = f'/release-assets/r59/{filename}'
        identity_destination = f'/assets/r59/{filename}'
        destination = f'{identity_destination}.br'
        assert not (PUBLIC / source.removeprefix('/')).exists()
        rewrite = next(
            rule for rule in rewrites
            if rule.get('source') == source and rule.get('has')
        )
        assert rewrite['destination'] == destination
        assert rewrite['has'] == [{
            'type': 'header',
            'key': 'Accept-Encoding',
            'value': accepted_brotli,
        }]

        fallback = next(
            rule for rule in rewrites
            if rule.get('source') == source and not rule.get('has')
        )
        assert fallback['destination'] == identity_destination

        base_rule = next(
            rule for rule in header_rules
            if rule.get('source') == source and not rule.get('has')
        )
        base_headers = {
            item['key'].lower(): item['value']
            for item in base_rule.get('headers', [])
        }
        assert base_headers == {
            'cache-control': 'public, max-age=31536000, immutable',
            'content-type': content_type,
            'vary': 'Accept-Encoding',
        }

        encoded_rule = next(
            rule for rule in header_rules
            if rule.get('source') == source and rule.get('has')
        )
        assert encoded_rule['has'] == rewrite['has']
        encoded_headers = {
            item['key'].lower(): item['value']
            for item in encoded_rule.get('headers', [])
        }
        assert encoded_headers == {
            'content-encoding': 'br',
        }


def test_ci_rebuilds_assets_for_every_release_affecting_change():
    for path_filter in (
        "'backend/**'", "'public/**'", "'scripts/**'", "'tests/**'",
        "'package.json'", "'package-lock.json'", "'vercel.json'",
    ):
        # The same trigger is required for pushes and pull requests.
        assert CI_WORKFLOW.count(path_filter) == 2
    assert 'uses: actions/setup-node@v4' in CI_WORKFLOW
    assert "node-version: '22'" in CI_WORKFLOW
    assert 'run: npm ci' in CI_WORKFLOW
    assert 'npm run build:frontend' in CI_WORKFLOW
    assert 'git diff --exit-code -- public/assets' in CI_WORKFLOW
    assert (
        'git ls-files --others --exclude-standard -- public/assets'
        in CI_WORKFLOW
    )
