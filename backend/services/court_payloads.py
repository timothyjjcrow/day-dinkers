"""Court payload normalization helpers."""
import re

DEFAULT_COUNTY_SLUG = 'orange-county'


def _slugify(value):
    cleaned = re.sub(r'[^a-z0-9]+', '-', str(value or '').strip().lower())
    cleaned = re.sub(r'-{2,}', '-', cleaned).strip('-')
    return cleaned


def normalize_county_slug(raw_value, fallback=DEFAULT_COUNTY_SLUG):
    slug = _slugify(raw_value)
    if not slug:
        return fallback
    if not slug.endswith('county'):
        slug = f'{slug}-county'
    return slug
