"""Practical visit facts with reviewed venue overrides and community provenance."""
import json

TEXT_FIELDS = {'entrance':400, 'parking':400, 'guest_access':240,
               'accessibility':300, 'rotation':300, 'court_labels':160}
CHOICE_FIELDS = {'access_type':{'public','fee','members','unknown'},
                 'play_access':{'drop_in','reservation','both','unknown'}}


def visiting_dict(raw):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or '{}')
        except (ValueError, TypeError):
            return {}
    return raw if isinstance(raw, dict) else {}


def normalize_visiting(raw):
    if not isinstance(raw, dict) or set(raw) - {*TEXT_FIELDS, *CHOICE_FIELDS}:
        raise ValueError('invalid_visiting_information')
    result = {}
    for key, value in raw.items():
        if not isinstance(value, str):
            raise ValueError('invalid_visiting_information')
        value = value.strip()
        if not value:
            continue
        if key in CHOICE_FIELDS and value not in CHOICE_FIELDS[key]:
            raise ValueError('invalid_visit_access')
        if key in TEXT_FIELDS and len(value) > TEXT_FIELDS[key]:
            raise ValueError('visiting_information_too_long')
        result[key] = value
    return result


def project_visiting(court, venue=None):
    community = visiting_dict(getattr(court, 'visitor_info', '{}'))
    official = visiting_dict((venue or {}).get('visitor_info'))
    return {'visitor_info':{**community, **official},
            'visitor_info_sources':{key:'venue' if key in official else 'community' for key in community.keys() | official.keys()}}
