"""Readable type and interaction geometry stay on shared design tokens."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
STYLES = (ROOT / 'public' / 'styles-v15.css').read_text()
USER_FACING_SOURCE = STYLES + (ROOT / 'public' / 'app-v15.js').read_text() + (ROOT / 'public' / 'index.html').read_text()


def test_user_facing_css_never_drops_below_the_twelve_pixel_type_floor():
    declarations = [
        (match.group(0), float(match.group('size')))
        for match in re.finditer(
            r'font-size\s*:\s*(?P<size>\d+(?:\.\d+)?)px', STYLES,
        )
    ]
    undersized = [declaration for declaration, size in declarations if size < 12]
    assert not undersized
    assert '--text-xs: 12px;' in STYLES
    assert STYLES.count('font-size: var(--text-xs)') >= 200
    assert not re.search(r'font-size\s*:\s*var\(--radius-', STYLES)
    assert not re.search(r'transform\s*:[^;]*var\(--radius-', STYLES)


def test_inline_user_facing_styles_keep_the_same_type_floor():
    declarations = [
        float(match.group('size'))
        for match in re.finditer(
            r'font-size\s*:\s*(?P<size>\d+(?:\.\d+)?)px', USER_FACING_SOURCE,
        )
    ]
    assert not [size for size in declarations if size < 12]
    assert not re.search(
        r'border-radius\s*:\s*(?:\d+(?:\.\d+)?px|\d+(?:\.\d+)?%)',
        USER_FACING_SOURCE,
    )


def test_component_corner_radii_use_the_shared_scale():
    declarations = re.findall(r'border-radius\s*:\s*([^;}]+)', STYLES)
    arbitrary = [
        value.strip() for value in declarations
        if re.search(r'\d+(?:\.\d+)?(?:px|%)', value)
    ]
    assert not arbitrary
    for token in (
        '--radius-xxs:', '--radius-xs:', '--radius-sm:', '--radius-md:',
        '--radius-lg:', '--radius-xl:', '--radius-pill:',
    ):
        assert token in STYLES
    radius_uses = re.findall(
        r'([\w-]+)\s*:\s*([^;{}]*var\(--radius-(?:xxs|xs|sm|md|lg|xl|pill)\)[^;{}]*)[;}]',
        STYLES,
    )
    assert not [declaration for declaration in radius_uses if declaration[0] != 'border-radius']
    for token in tuple(f'--space-{index}:' for index in range(1, 9)):
        assert token in STYLES


def test_known_text_and_competition_controls_keep_full_tap_targets():
    for selector in (
        '.court-inline-refresh', '.chat-image-retry', '.account-verify-email',
            '.rating-help-link', '.competition-entry-avatar {',
        '.competition-entry-person-name', '.competition-card-opponent .btn',
        '.competition-forfeit-controls summary', '.competition-match-schedule-action',
    ):
        start = STYLES.index(selector)
        rule = STYLES[start:STYLES.index('}', start)]
        assert 'var(--tap-min)' in rule, selector
