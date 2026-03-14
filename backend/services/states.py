"""US state metadata for multi-state court support."""

STATES = [
    {'slug': 'alabama', 'abbr': 'AL', 'name': 'Alabama'},
    {'slug': 'alaska', 'abbr': 'AK', 'name': 'Alaska'},
    {'slug': 'arizona', 'abbr': 'AZ', 'name': 'Arizona'},
    {'slug': 'arkansas', 'abbr': 'AR', 'name': 'Arkansas'},
    {'slug': 'california', 'abbr': 'CA', 'name': 'California'},
    {'slug': 'colorado', 'abbr': 'CO', 'name': 'Colorado'},
    {'slug': 'connecticut', 'abbr': 'CT', 'name': 'Connecticut'},
    {'slug': 'delaware', 'abbr': 'DE', 'name': 'Delaware'},
    {'slug': 'district-of-columbia', 'abbr': 'DC', 'name': 'District of Columbia'},
    {'slug': 'florida', 'abbr': 'FL', 'name': 'Florida'},
    {'slug': 'georgia', 'abbr': 'GA', 'name': 'Georgia'},
    {'slug': 'hawaii', 'abbr': 'HI', 'name': 'Hawaii'},
    {'slug': 'idaho', 'abbr': 'ID', 'name': 'Idaho'},
    {'slug': 'illinois', 'abbr': 'IL', 'name': 'Illinois'},
    {'slug': 'indiana', 'abbr': 'IN', 'name': 'Indiana'},
    {'slug': 'iowa', 'abbr': 'IA', 'name': 'Iowa'},
    {'slug': 'kansas', 'abbr': 'KS', 'name': 'Kansas'},
    {'slug': 'kentucky', 'abbr': 'KY', 'name': 'Kentucky'},
    {'slug': 'louisiana', 'abbr': 'LA', 'name': 'Louisiana'},
    {'slug': 'maine', 'abbr': 'ME', 'name': 'Maine'},
    {'slug': 'maryland', 'abbr': 'MD', 'name': 'Maryland'},
    {'slug': 'massachusetts', 'abbr': 'MA', 'name': 'Massachusetts'},
    {'slug': 'michigan', 'abbr': 'MI', 'name': 'Michigan'},
    {'slug': 'minnesota', 'abbr': 'MN', 'name': 'Minnesota'},
    {'slug': 'mississippi', 'abbr': 'MS', 'name': 'Mississippi'},
    {'slug': 'missouri', 'abbr': 'MO', 'name': 'Missouri'},
    {'slug': 'montana', 'abbr': 'MT', 'name': 'Montana'},
    {'slug': 'nebraska', 'abbr': 'NE', 'name': 'Nebraska'},
    {'slug': 'nevada', 'abbr': 'NV', 'name': 'Nevada'},
    {'slug': 'new-hampshire', 'abbr': 'NH', 'name': 'New Hampshire'},
    {'slug': 'new-jersey', 'abbr': 'NJ', 'name': 'New Jersey'},
    {'slug': 'new-mexico', 'abbr': 'NM', 'name': 'New Mexico'},
    {'slug': 'new-york', 'abbr': 'NY', 'name': 'New York'},
    {'slug': 'north-carolina', 'abbr': 'NC', 'name': 'North Carolina'},
    {'slug': 'north-dakota', 'abbr': 'ND', 'name': 'North Dakota'},
    {'slug': 'ohio', 'abbr': 'OH', 'name': 'Ohio'},
    {'slug': 'oklahoma', 'abbr': 'OK', 'name': 'Oklahoma'},
    {'slug': 'oregon', 'abbr': 'OR', 'name': 'Oregon'},
    {'slug': 'pennsylvania', 'abbr': 'PA', 'name': 'Pennsylvania'},
    {'slug': 'rhode-island', 'abbr': 'RI', 'name': 'Rhode Island'},
    {'slug': 'south-carolina', 'abbr': 'SC', 'name': 'South Carolina'},
    {'slug': 'south-dakota', 'abbr': 'SD', 'name': 'South Dakota'},
    {'slug': 'tennessee', 'abbr': 'TN', 'name': 'Tennessee'},
    {'slug': 'texas', 'abbr': 'TX', 'name': 'Texas'},
    {'slug': 'utah', 'abbr': 'UT', 'name': 'Utah'},
    {'slug': 'vermont', 'abbr': 'VT', 'name': 'Vermont'},
    {'slug': 'virginia', 'abbr': 'VA', 'name': 'Virginia'},
    {'slug': 'washington', 'abbr': 'WA', 'name': 'Washington'},
    {'slug': 'west-virginia', 'abbr': 'WV', 'name': 'West Virginia'},
    {'slug': 'wisconsin', 'abbr': 'WI', 'name': 'Wisconsin'},
    {'slug': 'wyoming', 'abbr': 'WY', 'name': 'Wyoming'},
]

STATE_BY_ABBR = {s['abbr']: s for s in STATES}
STATE_BY_SLUG = {s['slug']: s for s in STATES}
ABBR_TO_SLUG = {s['abbr']: s['slug'] for s in STATES}
SLUG_TO_ABBR = {s['slug']: s['abbr'] for s in STATES}


def normalize_state_slug(value):
    text = str(value or '').strip().lower().replace('_', '-').replace(' ', '-')
    while '--' in text:
        text = text.replace('--', '-')
    text = text.strip('-')
    if text.upper() in STATE_BY_ABBR:
        return ABBR_TO_SLUG[text.upper()]
    return text if text in STATE_BY_SLUG else ''


def state_name_for_abbr(abbr):
    entry = STATE_BY_ABBR.get(str(abbr or '').strip().upper())
    return entry['name'] if entry else ''


def state_name_for_slug(slug):
    entry = STATE_BY_SLUG.get(slug)
    return entry['name'] if entry else ''


def abbr_for_slug(slug):
    return SLUG_TO_ABBR.get(slug, '')
