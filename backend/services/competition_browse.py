"""Apply discovery controls before pagination, never to a loaded-page subset."""
from datetime import datetime, timezone
import math
from sqlalchemy import or_


def filter_competition_query(query, model, args):
    for key, comparison in (('starts_after', 'after'), ('starts_before', 'before')):
        raw = args.get(key)
        if not raw:
            continue
        try:
            when = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if when.tzinfo:
                when = when.astimezone(timezone.utc).replace(tzinfo=None)
        except (ValueError, TypeError):
            return query, 'invalid_competition_date'
        query = query.filter(model.starts_at >= when if comparison == 'after' else model.starts_at < when)
    event_type = args.get('event_type')
    if event_type:
        if event_type not in ('singles', 'doubles'):
            return query, 'invalid_event_type'
        query = query.filter(model.event_type == event_type) if hasattr(model, 'event_type') else query.filter(False) if event_type == 'doubles' else query
    if args.get('signup') == '1':
        query = query.filter(model.status == 'registration')
    raw_rating = args.get('self_rating')
    if raw_rating:
        try:
            rating = float(raw_rating)
            if not math.isfinite(rating) or not 1 <= rating <= 8:
                raise ValueError
        except (ValueError, TypeError):
            return query, 'invalid_self_rating'
        if hasattr(model, 'division_min_rating'):
            query = query.filter(or_(model.division_min_rating.is_(None), model.division_min_rating <= rating),
                or_(model.division_max_rating.is_(None), model.division_max_rating >= rating))
    return query, None
