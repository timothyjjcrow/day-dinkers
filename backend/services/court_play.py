"""A dated court view; player RSVPs and external registrations stay separate."""
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.models import Game, BusinessProfile, iso, utcnow, blocked_pair_ids
from backend.integrations.models import BusinessProviderConnection, BusinessScheduleOccurrence
from backend.integrations.services import publication_ready_connection_ids, public_occurrence_payload
from backend.services.business_schedule import dated_occurrences, local_today
from backend.services.business_governance import business_snapshot
from backend.services.business_visibility import public_business_query, hide_unsafe_public_links
from backend.services.court_hours import project_hours, interval_hours_conflict


def court_play_payload(court, viewer, start=None, end=None):
    from backend.routes.courts import friend_ids
    from backend.routes.games import _discovery_game_payload, _instant_game_discovery_allowed
    business = public_business_query().filter(BusinessProfile.court_id == court.id).first()
    venue = business.to_public_dict() if business else None
    hours = project_hours(court, venue)
    timezone = (hours.get('structured_hours') or {}).get('timezone') or (venue or {}).get('timezone') or ''
    try:
        zone = ZoneInfo(timezone) if timezone else UTC
    except (ZoneInfoNotFoundError, ValueError):
        timezone, zone = '', UTC
    start = start or local_today(timezone)
    end = end or start + timedelta(days=6)
    if end < start or (end-start).days > 30:
        raise ValueError('invalid_schedule_range')
    response = {'court_id':court.id, 'from':start.isoformat(), 'to':end.isoformat(),
                'timezone':timezone, 'closed':bool(court.closed), 'items':[], 'undated_count':0,
                'note':'Player plans do not reserve court space. Venue registration happens on the linked site.'}
    if court.closed:
        return response
    viewer_id = viewer.id if viewer else None
    friends = friend_ids(viewer_id) if viewer_id else set()
    hidden = blocked_pair_ids(viewer_id) if viewer_id else set()
    lower = datetime.combine(start, time.min, zone).astimezone(UTC).replace(tzinfo=None)
    upper = datetime.combine(end+timedelta(days=1), time.min, zone).astimezone(UTC).replace(tzinfo=None)
    now = utcnow()
    games = Game.query.filter(Game.court_id == court.id, Game.status == 'upcoming',
        Game.scheduled_at >= max(lower, now-timedelta(hours=2)), Game.scheduled_at < upper).order_by(Game.scheduled_at, Game.id).limit(1000).all()
    for game in games:
        player_ids = {player.user_id for player in game.players} | {game.creator_id}
        if not game.visible_to(viewer_id, friends) or (viewer_id not in player_ids and player_ids & hidden):
            continue
        if not _instant_game_discovery_allowed(game, viewer, friends):
            continue
        data = _discovery_game_payload(game, viewer, friends)
        if not viewer:
            # Keep public availability accurate (including held offers) while
            # withholding roster, owner and invitation identities.
            data = {key:data.get(key) for key in ('id','scheduled_at','ends_at','game_type',
                'max_players','spots_left','status','cost_cents','level_min','level_max','preferred_level')}
            data['players'] = []
        response['items'].append({'key':f'game:{game.id}', 'source':'player', 'source_label':'Player-organized',
            'title':game.title or ('Ranked match' if game.game_type == 'ranked' else 'Pickup session'),
            'starts_at':iso(game.scheduled_at), 'ends_at':data.get('ends_at'),
            'event_date':game.scheduled_at.replace(tzinfo=UTC).astimezone(zone).date().isoformat(),
            'timezone':timezone, 'game':data, 'status':'scheduled',
            'action':'open_session', 'action_label':'Open session' if data.get('is_joined') else 'View & join' if data.get('spots_left',0)>0 else 'View waitlist'})

    def add_venue(row, *, integrated=False):
        if row.get('status') == 'completed' or not row.get('active', True):
            return
        stamp = row.get('starts_at')
        on = row.get('event_date')
        if not on and stamp:
            try:
                on = datetime.fromisoformat(stamp.replace('Z','+00:00')).astimezone(zone).date().isoformat()
            except ValueError:
                pass
        if not on or not row.get('start_time') and not stamp:
            response['undated_count'] += 1
            return
        if not start.isoformat() <= on <= end.isoformat():
            return
        if row.get('ends_at'):
            try:
                if datetime.fromisoformat(row['ends_at'].replace('Z','+00:00')) <= now.replace(tzinfo=UTC):
                    return
            except ValueError:
                pass
        status = row.get('status') or 'scheduled'
        fresh = row.get('availability_fresh') is True
        unavailable = status == 'cancelled' or status == 'sold_out' or (fresh and row.get('spots_remaining') == 0)
        row = {**row, 'business_id':business.id, '_business_id':business.id, 'is_integrated':integrated}
        if integrated:
            row['occurrence_id'] = row.get('id')
        response['items'].append({'key':f'venue:{"provider" if integrated else "manual"}:{row.get("id")}:{on}',
            'source':'venue', 'source_label':f'Venue-run · {venue["name"]}',
            'title':row.get('title') or 'Venue session', 'starts_at':stamp, 'ends_at':row.get('ends_at'),
            'event_date':on, 'timezone':row.get('timezone') or timezone, 'status':'sold_out' if unavailable and status == 'scheduled' else status,
            'schedule':row, 'action':'external' if row.get('booking_url') and not unavailable else 'none',
            'action_label':'Register externally', 'source_note':'Connected schedule' if integrated else 'Venue-maintained schedule'})

    if business:
        snapshot = business.reviewed_snapshot_dict() if business.reviewed_public_snapshot else business_snapshot(business)
        rows = hide_unsafe_public_links(business, {'schedule':snapshot.get('schedule',[])})['schedule']
        for row in dated_occurrences(rows, start, end):
            add_venue(row)
        connections = BusinessProviderConnection.query.filter_by(business_id=business.id).all()
        ready = publication_ready_connection_ids(connections)
        by_id = {row.id:row for row in connections if row.id in ready}
        if ready:
            imported = BusinessScheduleOccurrence.query.filter(BusinessScheduleOccurrence.connection_id.in_(ready)).order_by(BusinessScheduleOccurrence.id).limit(500).all()
            for item in imported:
                add_venue(public_occurrence_payload(item, by_id[item.connection_id]), integrated=True)
    for offset in range((end-start).days+1):
        on = start+timedelta(days=offset)
        for index,row in enumerate(court.open_play_schedule_rows_list()):
            if row.get('weekday') != on.strftime('%a').lower():
                continue
            stamp = finish = None
            if timezone:
                try:
                    local = datetime.combine(on, time.fromisoformat(row['start']), zone)
                    stop = datetime.combine(on, time.fromisoformat(row['end']), zone)
                    if stop <= local:
                        stop += timedelta(days=1)
                    instant = local.astimezone(UTC)
                    if instant.astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None):
                        continue
                    stamp, finish = iso(instant.replace(tzinfo=None)), iso(stop.astimezone(UTC).replace(tzinfo=None))
                    if stop.astimezone(UTC) <= now.replace(tzinfo=UTC):
                        continue
                except (ValueError, KeyError):
                    continue
            response['items'].append({'key':f'community:{index}:{on}', 'source':'community',
                'source_label':'Community-listed open play', 'title':'Open play', 'event_date':on.isoformat(),
                'starts_at':stamp, 'ends_at':finish, 'timezone':timezone, 'status':'scheduled', 'window':row,
                'action':'plan', 'action_label':'Plan to go', 'source_note':'A listed time, not an RSVP or reserved place.'})
    for item in response['items']:
        conflict = interval_hours_conflict(hours, item.get('starts_at'), item.get('ends_at'))
        item['hours_conflict'] = conflict
        if not conflict:
            continue
        authority = 'Venue' if hours['hours_source'] == 'venue' else 'Community-listed court'
        item['hours_warning'] = f'{authority} hours show the court closed during this time. Confirm access with the venue or organizer.'
        if item['source'] == 'community':
            item['action'] = 'none'
            item['action_label'] = 'Check court hours'
        elif item['source'] == 'player':
            item['action_label'] = 'View session'
        elif item['action'] == 'external':
            item['action_label'] = 'Confirm with venue'

    def local_clock(row):
        if row.get('starts_at'):
            return datetime.fromisoformat(row['starts_at'].replace('Z','+00:00')).astimezone(zone).strftime('%H:%M')
        values = row.get('schedule') or row.get('window') or {}
        return values.get('start_time') or values.get('start') or '99:99'
    response['items'].sort(key=lambda row:(row['event_date'], local_clock(row), row['key']))
    response['has_more'] = len(response['items']) > 200 or len(games) == 1000
    response['items'] = response['items'][:200]
    return response
