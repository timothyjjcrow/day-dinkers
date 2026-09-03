"""Every API failure gets deliberate user copy instead of a raw code."""
from pathlib import Path


APP = (Path(__file__).resolve().parents[1] / 'public' / 'app-v15.js').read_text()


def test_unmapped_api_codes_use_safe_status_and_category_fallbacks():
    human_error = APP[
        APP.index('function humanError'):
        APP.index('const TOAST_GLYPH_ICONS')
    ]

    assert "ERROR_TEXT[code]" in human_error
    assert "normalized.endsWith('_not_found')" in human_error
    assert 'Number(status) === 403' in human_error
    assert 'Number(status) === 409' in human_error
    assert 'Number(status) === 400 || Number(status) === 422' in human_error
    assert 'Number(status) === 429' in human_error
    assert "code.replace(/_/g, ' ')" not in human_error
    assert 'Something went wrong. Try again' in human_error


def test_api_errors_keep_machine_code_separate_from_user_copy():
    api = APP[APP.index('async function api'):APP.index('function persistReplacementToken')]

    assert 'new Error(humanError(code, data, res.status))' in api
    assert 'err.code = code;' in api
    assert 'err.data = data;' in api
