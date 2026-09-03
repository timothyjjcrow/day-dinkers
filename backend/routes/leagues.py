"""Box leagues: rating-seeded boxes, round robin within each box, promotion/
relegation between rounds, champion crowned from box 1 at completion."""
from datetime import timedelta
import math

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy.exc import IntegrityError

from backend.app import db
from backend.models import (
    CompetitionResultEvent, Court, League, LeagueMatch, LeagueMember,
    Notification,
    award_new_badges, is_blocked_between, iso, notify, utcnow,
)
from backend.security import rate_limit

leagues_bp = Blueprint('leagues', __name__)

from backend.routes.auth import login_required  # noqa: E402
from backend.routes.competition_http import conditional_competition_detail  # noqa: E402
from backend.routes.courts import haversine_miles  # noqa: E402
from backend.routes.games import _page_args, _page_payload, _parse_scheduled_at  # noqa: E402

MIN_PLAYERS = 3


def _league_result_window_hours():
    return max(1, int(current_app.config.get(
        'LEAGUE_RESULT_AUTO_CONFIRM_HOURS', 24,
    )))


def _result_nudge_cooldown():
    return timedelta(minutes=max(1, int(current_app.config.get(
        'COMPETITION_RESULT_NUDGE_COOLDOWN_MINUTES', 30,
    ))))


def _result_review_deadline(reported_at):
    if not reported_at:
        return None
    return reported_at + timedelta(hours=_league_result_window_hours())


def _round_deadline(league):
    if not league.round_started_at or not league.round_days:
        return None
    return league.round_started_at + timedelta(days=league.round_days)


def _league_or_404(league_id):
    league = db.session.get(League, league_id)
    if not league:
        return None, (jsonify({'error': 'league_not_found'}), 404)
    return league, None


def _round_robin_pairs(user_ids):
    """All pairings within one box."""
    return [
        (user_ids[i], user_ids[j])
        for i in range(len(user_ids))
        for j in range(i + 1, len(user_ids))
    ]


def _boxes_of(league):
    """{box_number: [members sorted by standing]} for an active league."""
    boxes = {}
    for member in league.members:
        if member.box:
            boxes.setdefault(member.box, []).append(member)
    for box_members in boxes.values():
        box_members.sort(
            key=lambda m: (-m.points, -m.wins, -(m.user.rating if m.user else 0)),
        )
    return boxes


def _generate_round(league):
    """Round-robin matches inside every box for the league's current round."""
    for box_number, box_members in _boxes_of(league).items():
        for p1, p2 in _round_robin_pairs([m.user_id for m in box_members]):
            # Assign the relationship, not the FK — keeps league.matches in
            # sync for the payload built later in this same request.
            db.session.add(LeagueMatch(
                league=league, round=league.current_round,
                box=box_number, player1_id=p1, player2_id=p2,
            ))


@leagues_bp.post('/leagues')
@rate_limit(10, 3600)
@login_required
def create_league():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get('name') or '').strip()[:120]
    if len(name) < 3:
        return jsonify({'error': 'name_required'}), 400
    court = db.session.get(Court, int(payload.get('court_id') or 0))
    if not court:
        return jsonify({'error': 'court_not_found'}), 404
    starts_at = _parse_scheduled_at(payload.get('starts_at'))
    if not starts_at:
        return jsonify({'error': 'invalid_starts_at'}), 400

    try:
        box_size = int(payload.get('box_size') or 4)
    except (TypeError, ValueError):
        box_size = 4
    box_size = min(max(box_size, 3), 6)
    try:
        max_players = int(payload.get('max_players') or 16)
    except (TypeError, ValueError):
        max_players = 16
    max_players = min(max(max_players, MIN_PLAYERS), 48)
    try:
        round_days = int(payload.get('round_days') or 7)
    except (TypeError, ValueError):
        round_days = 7
    round_days = min(max(round_days, 3), 28)

    # Running under a club banner: members only.
    club = None
    if payload.get('club_id'):
        from backend.models import Club, ClubMember
        club = db.session.get(Club, int(payload.get('club_id')))
        if not club:
            return jsonify({'error': 'club_not_found'}), 404
        if not ClubMember.query.filter_by(
            club_id=club.id, user_id=g.current_user.id,
        ).first():
            return jsonify({'error': 'members_only'}), 403

    league = League(
        name=name,
        description=str(payload.get('description') or '').strip()[:500],
        court_id=court.id,
        organizer_id=g.current_user.id,
        club=club,
        starts_at=starts_at,
        box_size=box_size,
        round_days=round_days,
        max_players=max_players,
    )
    db.session.add(league)
    db.session.add(LeagueMember(league=league, user_id=g.current_user.id))
    db.session.flush()  # notifications below need league.id

    # Club members hear about their club's league first-class.
    if club:
        for member in club.members:
            if member.user_id == g.current_user.id:
                continue
            if is_blocked_between(g.current_user.id, member.user_id):
                continue
            notify(
                member.user_id,
                'club_game',
                f'{club.name}: new ladder league — {name}. Open for signups',
                related_user_id=g.current_user.id,
                related_league_id=league.id,
            )
    db.session.commit()
    return jsonify(_league_payload(league, g.current_user.id, detail=True)), 201


@leagues_bp.get('/leagues')
@login_required
def list_leagues():
    """Cursor-page a player's leagues or geographically nearby leagues."""
    limit, page_offset, page_error = _page_args(default=30, maximum=100)
    if page_error:
        return jsonify({'error': page_error}), 400
    court_id = request.args.get('court_id', type=int)
    mine_only = request.args.get('mine') is not None
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    if (lat is None) != (lng is None):
        return jsonify({'error': 'lat_and_lng_required_together'}), 400
    if lat is not None and not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return jsonify({'error': 'invalid_coordinates'}), 400
    try:
        radius = min(max(float(request.args.get('radius', 60)), 1), 250)
    except (TypeError, ValueError):
        return jsonify({'error': 'invalid_radius'}), 400

    mine = db.session.query(LeagueMember.league_id).filter(
        LeagueMember.user_id == g.current_user.id,
    )
    public_statuses = ['registration', 'active']
    if mine_only:
        query = League.query.filter(
            db.or_(
                League.id.in_(mine),
                League.organizer_id == g.current_user.id,
            ),
            League.status != 'cancelled',
        )
    elif lat is not None:
        lat_delta = radius / 69.0
        lng_delta = radius / max(0.1, 69.0 * math.cos(math.radians(lat)))
        query = League.query.join(Court).filter(
            League.status.in_(public_statuses),
            Court.latitude.between(lat - lat_delta, lat + lat_delta),
            Court.longitude.between(lng - lng_delta, lng + lng_delta),
        )
    else:
        # Backward-compatible unscoped API calls retain the earlier blend;
        # product surfaces use explicit mine and geographic feeds above.
        query = League.query.filter(db.or_(
            League.status.in_(public_statuses),
            db.and_(
                League.id.in_(mine),
                League.status != 'cancelled',
            ),
        ))
    if court_id:
        query = query.filter(League.court_id == court_id)
    active_first = db.case(
        (League.status.in_(public_statuses), 0),
        else_=1,
    )
    query = query.order_by(
        active_first.asc(), League.starts_at.asc(), League.id.desc(),
    )
    if lat is not None and not mine_only:
        batch_size = max(25, min(100, limit * 2))
        raw_offset = 0
        visible_before_page = 0
        items = []
        has_more = False
        while True:
            rows = query.offset(raw_offset).limit(batch_size).all()
            if not rows:
                break
            raw_offset += len(rows)
            for league in rows:
                court = league.court
                if not court or court.latitude is None or court.longitude is None:
                    continue
                distance = haversine_miles(
                    lat, lng, court.latitude, court.longitude,
                )
                if distance > radius:
                    continue
                if visible_before_page < page_offset:
                    visible_before_page += 1
                    continue
                if len(items) >= limit:
                    has_more = True
                    break
                item = _league_payload(league, g.current_user.id)
                item['distance_miles'] = round(distance, 1)
                items.append(item)
            if has_more or len(rows) < batch_size:
                break
        from backend.routes.games import _encode_page_cursor
        return jsonify({
            'items': items,
            'count': len(items),
            'total': None if has_more else page_offset + len(items),
            'has_more': has_more,
            'next_cursor': _encode_page_cursor(page_offset + len(items))
            if has_more else None,
        })

    total = query.count()
    leagues = query.offset(page_offset).limit(limit).all()
    return jsonify(_page_payload(
        [_league_payload(lg, g.current_user.id) for lg in leagues],
        limit=limit,
        offset=page_offset,
        total=total,
        already_sliced=True,
    ))


@leagues_bp.get('/leagues/<int:league_id>')
@login_required
def league_detail(league_id):
    league, err = _league_or_404(league_id)
    if err:
        return err
    requested_match_id = request.args.get('match_id', type=int)
    data = _league_payload(
        league,
        g.current_user.id,
        detail=True,
        detail_match_id=requested_match_id if requested_match_id and requested_match_id > 0 else None,
    )
    if league.member_for(g.current_user.id):
        from backend.models import LeagueChatRead, Message
        marker = LeagueChatRead.query.filter_by(
            user_id=g.current_user.id, league_id=league.id,
        ).first()
        data['chat_unread'] = Message.query.filter(
            Message.league_id == league.id,
            Message.id > (marker.last_read_message_id if marker else 0),
            Message.sender_id != g.current_user.id,
        ).count()
    return conditional_competition_detail(
        data,
        kind='league',
        entity_id=league.id,
        viewer_id=g.current_user.id,
    )


@leagues_bp.patch('/leagues/<int:league_id>')
@rate_limit(30, 3600)
@login_required
def update_league(league_id):
    """Let an organizer correct visible settings without rebuilding a season.

    Structural settings are intentionally registration-only: changing box or
    roster rules after match generation would make the published schedule and
    standings disagree.
    """
    league, err = _league_or_404(league_id)
    if err:
        return err
    if league.organizer_id != g.current_user.id:
        return jsonify({'error': 'organizer_only'}), 403
    if league.status in ('completed', 'cancelled'):
        return jsonify({'error': 'league_finished'}), 409

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'error': 'invalid_payload'}), 400
    structural = {'court_id', 'starts_at', 'box_size', 'round_days', 'max_players'}
    if league.status != 'registration' and structural.intersection(payload):
        return jsonify({'error': 'settings_locked_after_start'}), 409

    changed = []
    if 'name' in payload:
        name = str(payload.get('name') or '').strip()[:120]
        if len(name) < 3:
            return jsonify({'error': 'name_required'}), 400
        if name != league.name:
            league.name = name
            changed.append('name')
    if 'description' in payload:
        description = str(payload.get('description') or '').strip()[:500]
        if description != (league.description or ''):
            league.description = description
            changed.append('description')

    if 'court_id' in payload:
        raw_court_id = payload.get('court_id')
        if isinstance(raw_court_id, bool):
            return jsonify({'error': 'court_not_found'}), 404
        try:
            court_id = int(raw_court_id)
        except (TypeError, ValueError):
            return jsonify({'error': 'court_not_found'}), 404
        court = db.session.get(Court, court_id)
        if not court:
            return jsonify({'error': 'court_not_found'}), 404
        if court.id != league.court_id:
            league.court = court
            changed.append('home court')

    if 'starts_at' in payload:
        starts_at = _parse_scheduled_at(payload.get('starts_at'))
        if not starts_at:
            return jsonify({'error': 'invalid_starts_at'}), 400
        if starts_at != league.starts_at:
            league.starts_at = starts_at
            changed.append('start target')

    integer_rules = {
        'box_size': (3, 6, 'invalid_box_size'),
        'round_days': (3, 28, 'invalid_round_days'),
        'max_players': (MIN_PLAYERS, 48, 'invalid_max_players'),
    }
    for field, (minimum, maximum, error) in integer_rules.items():
        if field not in payload:
            continue
        raw = payload.get(field)
        if isinstance(raw, bool) or isinstance(raw, float) and not raw.is_integer():
            return jsonify({'error': error}), 400
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return jsonify({'error': error}), 400
        if not minimum <= value <= maximum:
            return jsonify({'error': error}), 400
        if field == 'max_players' and value < len(league.members):
            return jsonify({'error': 'max_players_below_roster'}), 409
        if value != getattr(league, field):
            setattr(league, field, value)
            changed.append(field.replace('_', ' '))

    if changed:
        summary = ', '.join(changed)
        for member in league.members:
            if member.user_id == g.current_user.id:
                continue
            notify(
                member.user_id,
                'league_update',
                f'{league.name} settings were updated',
                f'Changed: {summary}.',
                related_user_id=g.current_user.id,
                related_league_id=league.id,
                action_url=f'/#league/{league.id}',
            )
        db.session.commit()
    return jsonify(_league_payload(league, g.current_user.id, detail=True))


@leagues_bp.post('/leagues/<int:league_id>/join')
@rate_limit(30, 3600)
@login_required
def join_league(league_id):
    league, err = _league_or_404(league_id)
    if err:
        return err
    if league.status != 'registration':
        return jsonify({'error': 'registration_closed'}), 400
    if league.member_for(g.current_user.id):
        return jsonify({'error': 'already_joined'}), 400
    if len(league.members) >= league.max_players:
        return jsonify({'error': 'league_full'}), 400
    if is_blocked_between(g.current_user.id, league.organizer_id):
        return jsonify({'error': 'cannot_join'}), 403
    db.session.add(LeagueMember(league=league, user_id=g.current_user.id))
    if league.organizer_id != g.current_user.id:
        notify(
            league.organizer_id,
            'league_update',
            f'{g.current_user.display_name} joined {league.name}',
            related_user_id=g.current_user.id,
            related_league_id=league.id,
        )
    db.session.commit()
    return jsonify(_league_payload(league, g.current_user.id, detail=True))


@leagues_bp.post('/leagues/<int:league_id>/leave')
@rate_limit(30, 3600)
@login_required
def leave_league(league_id):
    league, err = _league_or_404(league_id)
    if err:
        return err
    if league.status != 'registration':
        return jsonify({'error': 'league_already_started'}), 400
    if league.organizer_id == g.current_user.id:
        return jsonify({'error': 'organizer_must_cancel'}), 400
    member = league.member_for(g.current_user.id)
    if not member:
        return jsonify({'error': 'not_a_member'}), 400
    league.members.remove(member)  # delete-orphan keeps the collection in sync
    db.session.commit()
    return jsonify({'left': True})


@leagues_bp.delete('/leagues/<int:league_id>/members/<int:user_id>')
@rate_limit(30, 3600)
@login_required
def remove_league_member(league_id, user_id):
    """Let the organizer correct the signup roster before matches exist."""
    league, err = _league_or_404(league_id)
    if err:
        return err
    if league.organizer_id != g.current_user.id:
        return jsonify({'error': 'organizer_only'}), 403
    if league.status != 'registration':
        return jsonify({'error': 'settings_locked_after_start'}), 409
    if user_id == league.organizer_id:
        return jsonify({'error': 'organizer_must_cancel'}), 400
    member = league.member_for(user_id)
    if not member:
        return jsonify({'error': 'league_member_not_found'}), 404

    league.members.remove(member)
    notify(
        user_id,
        'league_update',
        f'The organizer removed your signup from {league.name}',
        'You can sign up again while registration remains open and space is available.',
        related_user_id=g.current_user.id,
        related_league_id=league.id,
        action_url=f'/#league/{league.id}',
    )
    db.session.commit()
    return jsonify(_league_payload(league, g.current_user.id, detail=True))


@leagues_bp.post('/leagues/<int:league_id>/start')
@rate_limit(10, 3600)
@login_required
def start_league(league_id):
    """Seed boxes by rating and generate round 1."""
    league, err = _league_or_404(league_id)
    if err:
        return err
    if league.organizer_id != g.current_user.id:
        return jsonify({'error': 'organizer_only'}), 403
    if league.status != 'registration':
        return jsonify({'error': 'already_started'}), 400
    if len(league.members) < MIN_PLAYERS:
        return jsonify({'error': 'need_more_players'}), 400

    seeded = sorted(
        league.members,
        key=lambda m: -(m.user.rating if m.user else 0),
    )
    # Chunk into boxes; a too-small trailing box folds into the previous one.
    boxes = [seeded[i:i + league.box_size] for i in range(0, len(seeded), league.box_size)]
    if len(boxes) > 1 and len(boxes[-1]) < MIN_PLAYERS:
        boxes[-2].extend(boxes.pop())
    for box_number, box_members in enumerate(boxes, start=1):
        for member in box_members:
            member.box = box_number

    league.status = 'active'
    league.current_round = 1
    league.round_started_at = utcnow()
    _generate_round(league)

    for member in league.members:
        if member.user_id != g.current_user.id:
            notify(
                member.user_id,
                'league_update',
                f'{league.name} has started — you are in box {member.box}',
                related_user_id=g.current_user.id,
                related_league_id=league.id,
            )
    db.session.commit()
    return jsonify(_league_payload(league, g.current_user.id, detail=True))


UNRESOLVED_RESULT_STATES = frozenset({'awaiting_confirmation', 'disputed'})


def _locked_league(league_id):
    """Reload and row-lock a league before a result or round transition."""
    return (
        League.query.filter_by(id=league_id)
        .populate_existing()
        .with_for_update()
        .first()
    )


def _locked_league_match(league_id, match_id):
    return (
        LeagueMatch.query.filter_by(id=match_id, league_id=league_id)
        .populate_existing()
        .with_for_update()
        .first()
    )


def _result_request_payload():
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def _expected_result_version(payload):
    """Parse an optional optimistic-lock version from either supported key."""
    supplied = [
        payload[key]
        for key in ('expected_result_version', 'result_version')
        if key in payload
    ]
    if not supplied:
        return None, None
    if any(isinstance(value, bool) for value in supplied):
        return None, (jsonify({'error': 'invalid_result_version'}), 400)
    if any(
        isinstance(value, float) and not value.is_integer()
        for value in supplied
    ):
        return None, (jsonify({'error': 'invalid_result_version'}), 400)
    try:
        versions = [int(value) for value in supplied]
    except (TypeError, ValueError):
        return None, (jsonify({'error': 'invalid_result_version'}), 400)
    if any(version < 0 for version in versions) or len(set(versions)) != 1:
        return None, (jsonify({'error': 'invalid_result_version'}), 400)
    return versions[0], None


def _check_result_version(match, payload):
    expected, err = _expected_result_version(payload)
    if err:
        return err
    current = int(match.result_version or 0)
    if expected is not None and expected != current:
        return jsonify({
            'error': 'stale_result',
            'result_version': current,
        }), 409
    return None


def _commit_result_change(league_id, match_id):
    try:
        db.session.commit()
    except IntegrityError:
        # SQLite does not enforce FOR UPDATE. The audit event's unique
        # (competition, match, version) key is the fallback race detector; roll
        # back every standings/notification mutation and return a normal stale
        # result response instead of leaking an IntegrityError as a 500.
        db.session.rollback()
        match = db.session.get(LeagueMatch, match_id)
        if match and match.league_id == league_id:
            return jsonify({
                'error': 'stale_result',
                'result_version': int(match.result_version or 0),
            }), 409
        return jsonify({'error': 'stale_result'}), 409
    match = db.session.get(LeagueMatch, match_id)
    league = db.session.get(League, league_id)
    return jsonify(_decorate_league_match(
        league, match, match.to_dict(g.current_user.id), g.current_user.id,
    ))


def _parse_match_scores(payload):
    raw_score1 = payload.get('score1')
    raw_score2 = payload.get('score2')
    if raw_score1 is None or raw_score2 is None:
        return None, (jsonify({'error': 'scores_required'}), 400)
    if isinstance(raw_score1, bool) or isinstance(raw_score2, bool):
        return None, (jsonify({'error': 'invalid_scores'}), 400)
    if isinstance(raw_score1, float) and not raw_score1.is_integer():
        return None, (jsonify({'error': 'invalid_scores'}), 400)
    if isinstance(raw_score2, float) and not raw_score2.is_integer():
        return None, (jsonify({'error': 'invalid_scores'}), 400)
    try:
        score1 = int(raw_score1)
        score2 = int(raw_score2)
    except (TypeError, ValueError):
        return None, (jsonify({'error': 'scores_required'}), 400)
    if score1 == score2 or score1 < 0 or score2 < 0 or max(score1, score2) > 99:
        return None, (jsonify({'error': 'invalid_scores'}), 400)
    return (score1, score2), None


def _bump_result_version(match):
    match.result_version = int(match.result_version or 0) + 1
    return match.result_version


def _locked_match_members(match):
    members = (
        LeagueMember.query.filter(
            LeagueMember.league_id == match.league_id,
            LeagueMember.user_id.in_([match.player1_id, match.player2_id]),
        )
        .populate_existing()
        .with_for_update()
        .all()
    )
    return {member.user_id: member for member in members}


def _adjust_standings(match, winner_id, direction, members):
    """Apply (+1) or reverse (-1) one finalized league result."""
    if winner_id not in (match.player1_id, match.player2_id):
        return
    loser_id = match.player2_id if winner_id == match.player1_id else match.player1_id
    winner = members.get(winner_id)
    loser = members.get(loser_id)
    if winner:
        winner.points += 3 * direction
        winner.wins += direction
    if loser:
        loser.points += direction
        loser.losses += direction


def _finalize_match_score(match, score1, score2):
    """Replace any finalized result and apply its standings exactly once."""
    members = _locked_match_members(match)
    if match.winner_id is not None:
        _adjust_standings(match, match.winner_id, -1, members)
    match.score1 = score1
    match.score2 = score2
    match.winner_id = match.player1_id if score1 > score2 else match.player2_id
    _adjust_standings(match, match.winner_id, 1, members)


def _void_match_result(match):
    """Remove a finalized result from standings while retaining its evidence."""
    if match.winner_id is not None:
        members = _locked_match_members(match)
        _adjust_standings(match, match.winner_id, -1, members)
    match.winner_id = None


def _result_action_url(league, match):
    return f'/#league/{league.id}/match/{match.id}'


def _league_result_confirmer_id(match):
    if match.reported_by_id == match.player1_id:
        return match.player2_id
    if match.reported_by_id == match.player2_id:
        return match.player1_id
    return None


def _notify_league_result_users(league, match, user_ids, title, body='',
                                actor_id=None, unread_dedupe_key=''):
    for user_id in set(user_ids) - ({actor_id} if actor_id else set()) - {None}:
        notify(
            user_id,
            'league_match',
            title,
            body,
            related_user_id=actor_id,
            related_league_id=league.id,
            action_url=_result_action_url(league, match),
            unread_dedupe_key=unread_dedupe_key,
        )


def _decorate_league_match(league, match, data, user_id):
    state = match.effective_result_state()
    organizer = user_id == league.organizer_id
    confirmer_id = _league_result_confirmer_id(match)
    data['review_deadline_at'] = iso(_result_review_deadline(match.reported_at))
    data['can_nudge_result'] = bool(
        league.status == 'active'
        and match.round == league.current_round
        and state == 'awaiting_confirmation'
        and organizer
        and confirmer_id is not None
        and confirmer_id != user_id
    )
    data['nudge_available_at'] = iso(
        match.last_nudged_at + _result_nudge_cooldown()
    ) if match.last_nudged_at else None
    return data


def _league_action_summary(league, user_id):
    current = [
        match for match in league.matches
        if match.round == league.current_round
    ]
    unresolved = [
        match for match in current
        if match.effective_result_state() in UNRESOLVED_RESULT_STATES
    ]
    mine = [
        match for match in unresolved
        if match.effective_result_state() == 'awaiting_confirmation'
        and _league_result_confirmer_id(match) == user_id
    ]
    unplayed = [
        match for match in current
        if match.effective_result_state() == 'unreported'
        and user_id in (match.player1_id, match.player2_id)
    ]
    organizer_matches = unresolved if user_id == league.organizer_id else []
    action_matches = {
        match.id: match for match in mine + organizer_matches + unplayed
    }
    ordered_actions = sorted(
        action_matches.values(),
        key=lambda match: (match.reported_at or match.created_at, match.id),
    )
    ordered_unresolved = sorted(
        unresolved,
        key=lambda match: (match.reported_at or match.created_at, match.id),
    )
    start_action_pending = bool(
        league.status == 'registration'
        and league.organizer_id == user_id
        and len(league.members) >= MIN_PLAYERS
        and league.starts_at <= utcnow()
    )
    return {
        'my_confirmation_count': len(mine),
        'unresolved_result_count': len(unresolved),
        'oldest_waiting_at': iso(
            ordered_unresolved[0].reported_at or ordered_unresolved[0].created_at
        ) if ordered_unresolved else None,
        'my_unplayed_match_count': len(unplayed),
        'pending_action_count': len(action_matches) + int(start_action_pending),
        'action_match_id': ordered_actions[0].id if ordered_actions else None,
        'start_action_pending': start_action_pending,
    }


def _league_payload(league, user_id, *, detail=False, detail_match_id=None):
    data = league.to_dict(
        user_id,
        detail=detail,
        detail_match_id=detail_match_id,
    )
    data.update(_league_action_summary(league, user_id))
    data['round_deadline_at'] = iso(_round_deadline(league))
    data['result_auto_confirm_hours'] = _league_result_window_hours()
    if detail:
        matches = {match.id: match for match in league.matches}
        for item in data.get('matches', []):
            match = matches.get(item.get('id'))
            if match:
                _decorate_league_match(league, match, item, user_id)
        # Keep ``matches`` backward compatible as the current round (plus an
        # explicitly deep-linked match), while exposing prior rounds for the
        # detail screen's round picker and season history.
        history_matches = [
            match for match in league.matches
            if match.round < league.current_round
        ]
        history_events = CompetitionResultEvent.grouped_for_matches(
            'league', [match.id for match in history_matches],
        )
        data['match_history'] = [
            _decorate_league_match(
                league,
                match,
                match.to_dict(
                    user_id,
                    result_events=history_events.get(match.id, []),
                ),
                user_id,
            )
            for match in history_matches
        ]
        data['available_rounds'] = sorted({
            match.round for match in league.matches
        })
    return data


def _current_unresolved_matches(league, lock=False):
    query = LeagueMatch.query.filter_by(
        league_id=league.id, round=league.current_round,
    ).populate_existing()
    if lock:
        query = query.with_for_update()
    return [
        match for match in query.all()
        if match.effective_result_state() in UNRESOLVED_RESULT_STATES
    ]


@leagues_bp.post('/leagues/<int:league_id>/matches/<int:match_id>/score')
@rate_limit(30, 60)
@login_required
def report_match(league_id, match_id):
    """Submit a score for the opposing player's confirmation."""
    payload = _result_request_payload()
    league = _locked_league(league_id)
    if not league:
        return jsonify({'error': 'league_not_found'}), 404
    match = _locked_league_match(league.id, match_id)
    if not match:
        return jsonify({'error': 'match_not_found'}), 404
    if league.status != 'active' or match.round != league.current_round:
        return jsonify({'error': 'round_closed'}), 400
    if g.current_user.id not in (match.player1_id, match.player2_id):
        return jsonify({'error': 'players_only'}), 403

    version_err = _check_result_version(match, payload)
    if version_err:
        return version_err
    if match.effective_result_state() not in ('unreported', 'disputed'):
        return jsonify({'error': 'result_not_reportable'}), 409
    scores, score_err = _parse_match_scores(payload)
    if score_err:
        return score_err
    score1, score2 = scores

    now = utcnow()
    match.score1 = score1
    match.score2 = score2
    match.winner_id = None
    match.reported_by_id = g.current_user.id
    match.reported_at = now
    match.confirmed_by_id = None
    match.confirmed_at = None
    match.disputed_by_id = None
    match.disputed_at = None
    match.dispute_reason = ''
    match.resolution_kind = ''
    match.review_reminded_at = None
    match.stall_alerted_at = None
    match.last_nudged_at = None
    match.result_state = 'awaiting_confirmation'
    version = _bump_result_version(match)
    CompetitionResultEvent.record(
        'league', match.id, 'reported', version,
        actor_id=g.current_user.id,
        score1=score1,
        score2=score2,
        reason=str(payload.get('reason') or '').strip()[:500],
    )

    opponent_id = (
        match.player2_id if g.current_user.id == match.player1_id else match.player1_id
    )
    notify(
        opponent_id,
        'league_match',
        f'{g.current_user.display_name} reported {score1}–{score2} in {league.name}',
        'Confirm or dispute the result before standings update.',
        related_user_id=g.current_user.id,
        related_league_id=league.id,
        action_url=_result_action_url(league, match),
    )
    return _commit_result_change(league.id, match.id)


@leagues_bp.post('/leagues/<int:league_id>/matches/<int:match_id>/confirm')
@rate_limit(30, 60)
@login_required
def confirm_match_result(league_id, match_id):
    payload = _result_request_payload()
    league = _locked_league(league_id)
    if not league:
        return jsonify({'error': 'league_not_found'}), 404
    match = _locked_league_match(league.id, match_id)
    if not match:
        return jsonify({'error': 'match_not_found'}), 404
    if league.status != 'active' or match.round != league.current_round:
        return jsonify({'error': 'round_closed'}), 400
    if g.current_user.id not in (match.player1_id, match.player2_id):
        return jsonify({'error': 'players_only'}), 403

    version_err = _check_result_version(match, payload)
    if version_err:
        return version_err
    if match.effective_result_state() != 'awaiting_confirmation':
        return jsonify({'error': 'nothing_to_confirm'}), 409
    if (
        match.reported_by_id not in (match.player1_id, match.player2_id)
        or g.current_user.id == match.reported_by_id
    ):
        return jsonify({'error': 'opponent_confirmation_required'}), 403
    if match.score1 is None or match.score2 is None:
        return jsonify({'error': 'scores_required'}), 409

    _finalize_match_score(match, match.score1, match.score2)
    match.result_state = 'confirmed'
    match.confirmed_by_id = g.current_user.id
    match.confirmed_at = utcnow()
    match.resolution_kind = 'opponent_confirmation'
    version = _bump_result_version(match)
    CompetitionResultEvent.record(
        'league', match.id, 'confirmed', version,
        actor_id=g.current_user.id,
        score1=match.score1,
        score2=match.score2,
    )

    if match.reported_by_id != g.current_user.id:
        notify(
            match.reported_by_id,
            'league_match',
            f'{g.current_user.display_name} confirmed your {league.name} result',
            f'Final score: {match.score1}–{match.score2}. Standings are updated.',
            related_user_id=g.current_user.id,
            related_league_id=league.id,
            action_url=_result_action_url(league, match),
        )
    return _commit_result_change(league.id, match.id)


@leagues_bp.post('/leagues/<int:league_id>/matches/<int:match_id>/dispute')
@rate_limit(30, 60)
@login_required
def dispute_match_result(league_id, match_id):
    payload = _result_request_payload()
    league = _locked_league(league_id)
    if not league:
        return jsonify({'error': 'league_not_found'}), 404
    match = _locked_league_match(league.id, match_id)
    if not match:
        return jsonify({'error': 'match_not_found'}), 404
    if league.status != 'active' or match.round != league.current_round:
        return jsonify({'error': 'round_closed'}), 400
    if g.current_user.id not in (match.player1_id, match.player2_id):
        return jsonify({'error': 'players_only'}), 403

    version_err = _check_result_version(match, payload)
    if version_err:
        return version_err
    if match.effective_result_state() != 'awaiting_confirmation':
        return jsonify({'error': 'nothing_to_dispute'}), 409
    if (
        match.reported_by_id not in (match.player1_id, match.player2_id)
        or g.current_user.id == match.reported_by_id
    ):
        return jsonify({'error': 'opponent_dispute_required'}), 403

    reason = str(payload.get('reason') or '').strip()[:500]
    match.result_state = 'disputed'
    match.disputed_by_id = g.current_user.id
    match.disputed_at = utcnow()
    match.dispute_reason = reason
    match.resolution_kind = ''
    match.stall_alerted_at = None
    version = _bump_result_version(match)
    CompetitionResultEvent.record(
        'league', match.id, 'disputed', version,
        actor_id=g.current_user.id,
        score1=match.score1,
        score2=match.score2,
        reason=reason,
    )

    recipients = {match.reported_by_id, league.organizer_id} - {g.current_user.id, None}
    for recipient_id in recipients:
        notify(
            recipient_id,
            'league_match',
            f'{g.current_user.display_name} disputed a {league.name} result',
            reason or f'The reported score was {match.score1}–{match.score2}.',
            related_user_id=g.current_user.id,
            related_league_id=league.id,
            action_url=_result_action_url(league, match),
        )
    return _commit_result_change(league.id, match.id)


@leagues_bp.post('/leagues/<int:league_id>/matches/<int:match_id>/nudge')
@rate_limit(30, 3600)
@login_required
def nudge_match_result(league_id, match_id):
    payload = _result_request_payload()
    league = _locked_league(league_id)
    if not league:
        return jsonify({'error': 'league_not_found'}), 404
    match = _locked_league_match(league.id, match_id)
    if not match:
        return jsonify({'error': 'match_not_found'}), 404
    if league.organizer_id != g.current_user.id:
        return jsonify({'error': 'organizer_only'}), 403
    if league.status != 'active' or match.round != league.current_round:
        return jsonify({'error': 'round_closed'}), 400
    version_err = _check_result_version(match, payload)
    if version_err:
        return version_err
    if match.effective_result_state() != 'awaiting_confirmation':
        return jsonify({'error': 'nothing_to_confirm'}), 409

    confirmer_id = _league_result_confirmer_id(match)
    if not confirmer_id or confirmer_id == g.current_user.id:
        return jsonify({'error': 'not_allowed'}), 409
    now = utcnow()
    cooldown = _result_nudge_cooldown()
    available_at = (
        match.last_nudged_at + cooldown if match.last_nudged_at else now
    )
    if available_at > now:
        data = _decorate_league_match(
            league, match, match.to_dict(g.current_user.id), g.current_user.id,
        )
        data.update({
            'already_sent': True,
            'retry_after_seconds': max(
                1, int((available_at - now).total_seconds() + 0.999),
            ),
        })
        return jsonify(data)

    bucket_seconds = max(60, int(cooldown.total_seconds()))
    _notify_league_result_users(
        league,
        match,
        {confirmer_id},
        f'{league.name}: please confirm the submitted score',
        f'Reported score: {match.score1}–{match.score2}.',
        actor_id=g.current_user.id,
        unread_dedupe_key=(
            f'league-result-nudge:{match.id}:v{match.result_version}:'
            f'{int(now.timestamp() // bucket_seconds)}'
        ),
    )
    match.last_nudged_at = now
    db.session.commit()
    data = _decorate_league_match(
        league, match, match.to_dict(g.current_user.id), g.current_user.id,
    )
    data.update({'already_sent': False, 'retry_after_seconds': bucket_seconds})
    return jsonify(data)


@leagues_bp.post('/leagues/<int:league_id>/matches/<int:match_id>/resolve')
@rate_limit(30, 60)
@login_required
def resolve_match_result(league_id, match_id):
    """Organizer finalizes/corrects a score, or voids it, with an audit reason."""
    payload = _result_request_payload()
    league = _locked_league(league_id)
    if not league:
        return jsonify({'error': 'league_not_found'}), 404
    match = _locked_league_match(league.id, match_id)
    if not match:
        return jsonify({'error': 'match_not_found'}), 404
    if league.organizer_id != g.current_user.id:
        return jsonify({'error': 'organizer_only'}), 403
    if league.status != 'active' or match.round != league.current_round:
        return jsonify({'error': 'round_closed'}), 400

    version_err = _check_result_version(match, payload)
    if version_err:
        return version_err
    state = match.effective_result_state()
    if state not in ('awaiting_confirmation', 'disputed', 'confirmed'):
        return jsonify({'error': 'nothing_to_resolve'}), 409
    reason = str(payload.get('reason') or '').strip()[:500]
    if not reason:
        return jsonify({'error': 'reason_required'}), 400

    resolution = str(
        payload.get('resolution') or payload.get('action')
        or payload.get('result_state') or payload.get('resolution_kind') or ''
    ).strip().lower()
    void_value = payload.get('void')
    voided = (
        void_value is True or void_value == 1
        or str(void_value or '').strip().lower() in ('1', 'true', 'yes')
        or resolution in ('void', 'voided')
    )
    was_confirmed = state == 'confirmed' and match.winner_id is not None
    now = utcnow()

    if voided:
        _void_match_result(match)
        match.result_state = 'void'
        match.resolution_kind = 'organizer_void'
        action = 'voided'
    else:
        scores, score_err = _parse_match_scores(payload)
        if score_err:
            return score_err
        score1, score2 = scores
        _finalize_match_score(match, score1, score2)
        match.result_state = 'confirmed'
        match.resolution_kind = (
            'organizer_correction' if was_confirmed else 'organizer_resolution'
        )
        action = 'corrected' if was_confirmed else 'resolved'

    match.confirmed_by_id = g.current_user.id
    match.confirmed_at = now
    match.disputed_by_id = None
    match.disputed_at = None
    match.dispute_reason = ''
    version = _bump_result_version(match)
    CompetitionResultEvent.record(
        'league', match.id, action, version,
        actor_id=g.current_user.id,
        score1=match.score1,
        score2=match.score2,
        reason=reason,
    )

    title = (
        f'{league.name}: the organizer marked your match as not played'
        if voided else
        f'{league.name}: the organizer finalized your match {match.score1}–{match.score2}'
    )
    for recipient_id in {match.player1_id, match.player2_id} - {g.current_user.id}:
        notify(
            recipient_id,
            'league_match',
            title,
            reason,
            related_user_id=g.current_user.id,
            related_league_id=league.id,
            action_url=_result_action_url(league, match),
        )
    return _commit_result_change(league.id, match.id)


def maintain_league_results(now=None):
    """Remind reviewers, auto-confirm quiet scores, and flag stale disputes."""
    now = now or utcnow()
    window = timedelta(hours=_league_result_window_hours())
    half_cutoff = now - (window / 2)
    candidate_ids = (
        db.session.query(LeagueMatch.league_id, LeagueMatch.id)
        .join(League, League.id == LeagueMatch.league_id)
        .filter(
            League.status == 'active',
            LeagueMatch.result_state.in_([
                'awaiting_confirmation', 'disputed',
            ]),
            LeagueMatch.reported_at.is_not(None),
            LeagueMatch.reported_at <= half_cutoff,
        )
        .order_by(LeagueMatch.reported_at.asc(), LeagueMatch.id.asc())
        .limit(200)
        .all()
    )
    db.session.rollback()
    outcomes = {'reminded': 0, 'auto_confirmed': 0, 'stalled': 0}

    for league_id, match_id in candidate_ids:
        league = _locked_league(league_id)
        if not league or league.status != 'active':
            db.session.rollback()
            continue
        match = _locked_league_match(league.id, match_id)
        if not match or match.round != league.current_round:
            db.session.rollback()
            continue
        state = match.effective_result_state()
        try:
            if state == 'awaiting_confirmation':
                if not match.reported_at:
                    db.session.rollback()
                    continue
                deadline = _result_review_deadline(match.reported_at)
                if deadline and deadline <= now:
                    scores, score_error = _parse_match_scores({
                        'score1': match.score1, 'score2': match.score2,
                    })
                    if score_error:
                        db.session.rollback()
                        continue
                    _finalize_match_score(match, *scores)
                    match.result_state = 'confirmed'
                    match.confirmed_by_id = None
                    match.confirmed_at = now
                    match.resolution_kind = 'automatic_timeout'
                    version = _bump_result_version(match)
                    CompetitionResultEvent.record(
                        'league', match.id, 'auto_confirmed', version,
                        actor_id=None,
                        score1=match.score1,
                        score2=match.score2,
                    )
                    _notify_league_result_users(
                        league,
                        match,
                        {
                            match.player1_id, match.player2_id,
                            league.organizer_id,
                        },
                        f'{league.name}: score confirmed automatically',
                        f'Final score: {match.score1}–{match.score2}. Standings are updated.',
                        unread_dedupe_key=(
                            f'league-result-auto:{match.id}:v{version}'
                        ),
                    )
                    outcomes['auto_confirmed'] += 1
                elif (
                    match.reported_at + (window / 2) <= now
                    and match.review_reminded_at is None
                ):
                    confirmer_id = _league_result_confirmer_id(match)
                    _notify_league_result_users(
                        league,
                        match,
                        {confirmer_id},
                        f'{league.name}: this score still needs your confirmation',
                        f'Reported score: {match.score1}–{match.score2}.',
                        unread_dedupe_key=(
                            f'league-result-half:{match.id}:'
                            f'v{match.result_version}'
                        ),
                    )
                    match.review_reminded_at = now
                    outcomes['reminded'] += 1
                else:
                    db.session.rollback()
                    continue
            elif state == 'disputed':
                disputed_from = match.disputed_at or match.reported_at
                if (
                    not disputed_from
                    or disputed_from + window > now
                    or match.stall_alerted_at is not None
                ):
                    db.session.rollback()
                    continue
                _notify_league_result_users(
                    league,
                    match,
                    {league.organizer_id},
                    f'{league.name}: a disputed score needs your decision',
                    f'Reported score: {match.score1}–{match.score2}.',
                    unread_dedupe_key=(
                        f'league-result-stall:{match.id}:'
                        f'v{match.result_version}'
                    ),
                )
                match.stall_alerted_at = now
                outcomes['stalled'] += 1
            else:
                db.session.rollback()
                continue
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            continue
    return outcomes


def _do_advance(league, actor_id=None, now=None):
    """Close the round: box winners move up, last place moves down, next
    round's matches are generated. Unplayed matches simply score no points."""
    if _current_unresolved_matches(league, lock=True):
        return False
    boxes = _boxes_of(league)
    box_numbers = sorted(boxes)
    for box_number in box_numbers:
        standing = boxes[box_number]
        if box_number > box_numbers[0] and standing:
            standing[0].box = box_number - 1          # winner moves up
        if box_number < box_numbers[-1] and len(standing) > 1:
            standing[-1].box = box_number + 1         # last place drops
    league.current_round += 1
    league.round_started_at = now or utcnow()
    _generate_round(league)

    for member in league.members:
        if member.user_id != actor_id:
            notify(
                member.user_id,
                'league_update',
                f'{league.name}: round {league.current_round} is up — you are in box {member.box}',
                related_user_id=actor_id,
                related_league_id=league.id,
            )
    return True


def advance_due_league_rounds(now=None):
    """Scheduled round advancement plus once-per-round deadline reminders."""
    now = now or utcnow()
    # ``starts_at`` is a planning target, not an automatic state transition.
    # Once it arrives, give the organizer one durable, actionable reminder.
    for league in League.query.filter(
        League.status == 'registration',
        League.starts_at <= now,
    ).all():
        if len(league.members) < MIN_PLAYERS:
            continue
        reminder_title = f'{league.name} is ready for you to start'
        already_sent = Notification.query.filter_by(
            user_id=league.organizer_id,
            kind='league_update',
            related_league_id=league.id,
            title=reminder_title,
        ).first()
        if already_sent:
            continue
        notify(
            league.organizer_id,
            'league_update',
            reminder_title,
            'Review the field, then start round 1 when everyone is ready.',
            related_league_id=league.id,
            action_url=f'/#league/{league.id}',
            unread_dedupe_key=f'league-ready-to-start:{league.id}',
        )
    for league in League.query.filter_by(status='active').all():
        if not league.round_started_at:
            league.round_started_at = now  # legacy rows from before this column
            continue
        deadline = league.round_started_at + timedelta(days=league.round_days)
        if now >= deadline:
            # Re-lock and refresh before closing so a concurrent report cannot
            # slip into the old round after the unresolved-result check.
            league = _locked_league(league.id)
            if not league or league.status != 'active' or not league.round_started_at:
                continue
            deadline = league.round_started_at + timedelta(days=league.round_days)
            if now < deadline:
                continue
            unresolved = _current_unresolved_matches(league, lock=True)
            if unresolved:
                if int(league.deadline_alerted_round or 0) < league.current_round:
                    ordered = sorted(
                        unresolved,
                        key=lambda match: (
                            match.reported_at or match.created_at, match.id,
                        ),
                    )
                    count = len(ordered)
                    oldest = ordered[0]
                    notify(
                        league.organizer_id,
                        'league_match',
                        f'Round {league.current_round} can’t close: '
                        f'{count} result needs your decision' if count == 1 else
                        f'Round {league.current_round} can’t close: '
                        f'{count} results need your decision',
                        'Open the oldest unresolved result to set a final score or mark it not played.',
                        related_league_id=league.id,
                        action_url=_result_action_url(league, oldest),
                        unread_dedupe_key=(
                            f'league-round-stall:{league.id}:'
                            f'r{league.current_round}'
                        ),
                    )
                    league.deadline_alerted_round = league.current_round
                continue
            _do_advance(league, actor_id=None, now=now)
            continue
        if now >= deadline - timedelta(days=2):
            days_left = max(1, (deadline - now).days + (1 if (deadline - now).seconds else 0))
            unplayed_by_user = {}
            for match in league.matches:
                if (
                    match.round == league.current_round
                    and match.effective_result_state() == 'unreported'
                ):
                    unplayed_by_user.setdefault(match.player1_id, 0)
                    unplayed_by_user.setdefault(match.player2_id, 0)
                    unplayed_by_user[match.player1_id] += 1
                    unplayed_by_user[match.player2_id] += 1
            for member in league.members:
                pending = unplayed_by_user.get(member.user_id, 0)
                if not pending or member.reminded_round >= league.current_round:
                    continue
                member.reminded_round = league.current_round
                notify(
                    member.user_id,
                    'league_match',
                    f'{league.name}: {days_left} day{"" if days_left == 1 else "s"} left '
                    f'to play your {pending} box match{"" if pending == 1 else "es"}',
                    related_league_id=league.id,
                )
    db.session.commit()


@leagues_bp.post('/leagues/<int:league_id>/advance')
@rate_limit(10, 3600)
@login_required
def advance_round(league_id):
    league = _locked_league(league_id)
    if not league:
        return jsonify({'error': 'league_not_found'}), 404
    if league.organizer_id != g.current_user.id:
        return jsonify({'error': 'organizer_only'}), 403
    if league.status != 'active':
        return jsonify({'error': 'not_active'}), 400
    if not _do_advance(league, actor_id=g.current_user.id):
        return jsonify({'error': 'unresolved_results'}), 409
    db.session.commit()
    return jsonify(_league_payload(league, g.current_user.id, detail=True))


@leagues_bp.post('/leagues/<int:league_id>/complete')
@rate_limit(10, 3600)
@login_required
def complete_league(league_id):
    """End the season: whoever tops box 1 is champion."""
    league = _locked_league(league_id)
    if not league:
        return jsonify({'error': 'league_not_found'}), 404
    if league.organizer_id != g.current_user.id:
        return jsonify({'error': 'organizer_only'}), 403
    if league.status != 'active':
        return jsonify({'error': 'not_active'}), 400
    if _current_unresolved_matches(league, lock=True):
        return jsonify({'error': 'unresolved_results'}), 409

    league.status = 'completed'
    league.completed_at = utcnow()
    boxes = _boxes_of(league)
    champion = boxes[min(boxes)][0] if boxes else None
    # Assign the relationship, not the FK — champion_name serializes in this
    # same request.
    league.champion = champion.user if champion else None
    for member in league.members:
        if member.user_id == g.current_user.id:
            continue
        is_champ = champion and member.user_id == champion.user_id
        notify(
            member.user_id,
            'league_update',
            (f'You won {league.name}! Champion of the season'
             if is_champ else f'{league.name} has wrapped up — thanks for playing'),
            related_user_id=g.current_user.id,
            related_league_id=league.id,
        )
    if champion and champion.user:
        award_new_badges(champion.user)
    db.session.commit()
    data = _league_payload(league, g.current_user.id, detail=True)
    data['champion'] = champion.to_dict() if champion else None
    return jsonify(data)


@leagues_bp.post('/leagues/<int:league_id>/cancel')
@rate_limit(10, 3600)
@login_required
def cancel_league(league_id):
    league, err = _league_or_404(league_id)
    if err:
        return err
    if league.organizer_id != g.current_user.id:
        return jsonify({'error': 'organizer_only'}), 403
    if league.status in ('completed', 'cancelled'):
        return jsonify({'error': 'already_finished'}), 400
    league.status = 'cancelled'
    for member in league.members:
        if member.user_id != g.current_user.id:
            notify(
                member.user_id,
                'league_update',
                f'{league.name} was cancelled',
                related_user_id=g.current_user.id,
                related_league_id=league.id,
            )
    db.session.commit()
    return jsonify({'cancelled': True})


@leagues_bp.get('/leagues/<int:league_id>/chat')
@login_required
def league_chat(league_id):
    from backend.services.conversations import (
        advance_conversation_read, conversation_ref,
    )
    league, err = _league_or_404(league_id)
    if err:
        return err
    if not league.member_for(g.current_user.id):
        return jsonify({'error': 'members_only'}), 403
    conversation = conversation_ref('league', league.id)
    from backend.routes.chat import (
        chat_messages_window, chat_read_marker_target, chat_window_args,
        room_heart_counts,
    )
    window, window_err = chat_window_args()
    if window_err:
        return window_err
    since_id, before_id, history_limit = window
    query = conversation.message_query()
    messages, has_more, has_older, next_before_id = chat_messages_window(
        query, since_id, before_id, history_limit=history_limit,
    )

    # Reading the room marks it read — powers the league-screen badge.
    latest_id = chat_read_marker_target(
        query, messages, since_id, before_id, has_more,
    )
    advance_conversation_read(conversation, g.current_user.id, latest_id)
    db.session.commit()

    return jsonify({
        'conversation': conversation.to_dict(league.name),
        'league': {'id': league.id, 'name': league.name},
        'items': [m.to_dict() for m in messages],
        'heart_counts': room_heart_counts('league_id', league_id),
        'has_more': has_more,
        'has_older': has_older,
        'next_before_id': next_before_id,
    })


@leagues_bp.post('/leagues/<int:league_id>/chat')
@rate_limit(60, 60)
@login_required
def send_league_message(league_id):
    from backend.models import Notification
    from backend.services.conversations import conversation_ref
    league, err = _league_or_404(league_id)
    if err:
        return err
    if not league.member_for(g.current_user.id):
        return jsonify({'error': 'members_only'}), 403
    conversation = conversation_ref('league', league.id)
    from backend.routes.chat import prepare_chat_message
    message, replayed, body, err = prepare_chat_message(
        request.get_json(silent=True), g.current_user.id,
        conversation=conversation,
    )
    if err:
        return err
    if replayed:
        return jsonify(conversation.decorate_message(message, league.name)), 200

    # One unread ping per league per member, mirroring the other room chats.
    for member in league.members:
        if member.user_id == g.current_user.id:
            continue
        already_pinged = Notification.query.filter_by(
            user_id=member.user_id,
            kind='league_message',
            related_league_id=league.id,
            read=False,
        ).first()
        if not already_pinged:
            notify(
                member.user_id,
                'league_message',
                f'{g.current_user.display_name} in {league.name}',
                body[:140],
                related_user_id=g.current_user.id,
                related_league_id=league.id,
                unread_dedupe_key=f'league_message:{league.id}',
            )
    db.session.commit()
    return jsonify(conversation.decorate_message(message, league.name)), 201
