"""Privacy-safe venue activity comparisons; historical unknowns stay unknown."""
from datetime import timedelta
from sqlalchemy import func, or_
from backend.app import db
from backend.integrations.models import BusinessBookingEvent

BOOKING_ACTIONS = ('booking', 'membership', 'event', 'open_play')
SESSION_ACTIONS = (*BOOKING_ACTIONS, 'lesson')


def activity_comparison(business_id, since, until):
    period = until - since
    previous_since = since - period
    counts = db.session.query(BusinessBookingEvent.action, func.count(BusinessBookingEvent.id)).filter(
        BusinessBookingEvent.business_id == business_id,
        BusinessBookingEvent.event_type == 'click',
        BusinessBookingEvent.occurred_at >= previous_since,
        BusinessBookingEvent.occurred_at < since,
    ).group_by(BusinessBookingEvent.action).all()
    previous = dict(counts)
    previous_summary = {'profile_views': previous.get('profile_view', 0), 'booking_clicks': sum(previous.get(action, 0) for action in BOOKING_ACTIONS), 'lesson_clicks':previous.get('lesson', 0)}
    rows = db.session.query(
        BusinessBookingEvent.schedule_item_id, BusinessBookingEvent.schedule_occurrence_on,
        BusinessBookingEvent.occurrence_id, BusinessBookingEvent.subject_label,
        func.count(BusinessBookingEvent.id).label('clicks'),
    ).filter(
        BusinessBookingEvent.business_id == business_id,
        BusinessBookingEvent.event_type == 'click',
        BusinessBookingEvent.action.in_(SESSION_ACTIONS),
        BusinessBookingEvent.occurred_at >= since,
        BusinessBookingEvent.occurred_at < until,
        or_(BusinessBookingEvent.schedule_occurrence_on.is_not(None), BusinessBookingEvent.occurrence_id.is_not(None)),
    ).group_by(BusinessBookingEvent.schedule_item_id, BusinessBookingEvent.schedule_occurrence_on, BusinessBookingEvent.occurrence_id, BusinessBookingEvent.subject_label).order_by(func.count(BusinessBookingEvent.id).desc(), BusinessBookingEvent.subject_label.asc()).limit(10).all()
    return {'previous': previous_summary, 'previous_since':previous_since.isoformat()+'Z', 'until':until.isoformat()+'Z', 'top_sessions':[
        {'title':label or 'Session name not recorded', 'date':on.isoformat() if on else None, 'source':'venue schedule' if on else 'connected schedule', 'clicks':int(clicks)} for _,on,_,label,clicks in rows
    ], 'top_sessions_note':'Tracks links with a recorded session. General booking links and older unattributed clicks are excluded.'}
