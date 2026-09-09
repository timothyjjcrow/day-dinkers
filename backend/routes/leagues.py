"""Box leagues: rating-seeded boxes, round robin within each box, promotion/
relegation between rounds, champion crowned from box 1 at completion."""
from datetime import timedelta
import math
import json
import hashlib

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy.exc import IntegrityError

from backend.app import db
from backend.models import (
    CompetitionResultEvent, Court, League, LeagueMatch, LeagueMember, Game, GamePlayer, User,
    Tournament, TournamentEntry, TournamentMatch,
    Notification,
    award_new_badges, is_blocked_between, iso, notify, utcnow,
)
from backend.security import rate_limit
from backend.services.competition_browse import filter_competition_query
from backend.services.player_schedule import schedule_review_needed, schedule_batch_review_needed

leagues_bp = Blueprint('leagues', __name__)

from backend.routes.auth import login_required  # noqa: E402
from backend.routes.competition_http import conditional_competition_detail  # noqa: E402
from backend.routes.courts import haversine_miles  # noqa: E402
from backend.routes.games import _page_args, _page_payload, _parse_scheduled_at, _is_standard_pickleball_score  # noqa: E402

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
    if league.round_deadline_override_at:
        return league.round_deadline_override_at
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
        if member.box and not member.withdrawn_at:
            boxes.setdefault(member.box, []).append(member)
    for box_members in boxes.values():
        box_members.sort(
            key=lambda m: (-m.points, -m.wins, -(m.user.rating if m.user else 0)),
        )
    return boxes


def _generate_round(league):
    """Round-robin matches inside every box for the league's current round."""
    for box_number, box_members in _boxes_of(league).items():
        for p1, p2 in _round_robin_pairs([m.user_id for m in box_members if m.unavailable_round != league.current_round]):
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
    if not isinstance(payload, dict):
        return jsonify({'error': 'invalid_payload'}), 400
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
    total_rounds = payload.get('total_rounds', 6)
    if isinstance(total_rounds, bool) or not isinstance(total_rounds, int) or not 1 <= total_rounds <= 52:
        return jsonify({'error': 'invalid_total_rounds'}), 400

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
        total_rounds=total_rounds,
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
    query, filter_error = filter_competition_query(query, League, request.args)
    if filter_error:
        return jsonify({'error': filter_error}), 400
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
    structural = {'court_id', 'starts_at', 'box_size', 'round_days', 'max_players', 'total_rounds'}
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
        'total_rounds': (1, 52, 'invalid_total_rounds'),
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
    league.round_deadline_override_at = None
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


def _parse_match_scores(payload, *, stored=False):
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
    if not stored and not _is_standard_pickleball_score(score1, score2) and payload.get('accept_nonstandard_score') is not True:
        return None, (jsonify({'error': 'nonstandard_pickleball_score', 'can_confirm': True}), 422)
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
    data.update(_league_schedule_payload(league, match, user_id))
    can_audit = user_id in (match.player1_id, match.player2_id, league.organizer_id)
    closed_review = json.loads(match.closed_round_review or '{}')
    data['closed_round_review'] = closed_review if can_audit else {}
    data['can_review_closed_round'] = bool(can_audit and _closed_review_eligible(league, match))
    state = match.effective_result_state()
    organizer = user_id == league.organizer_id
    confirmer_id = _league_result_confirmer_id(match)
    data['review_deadline_at'] = iso(_result_review_deadline(match.reported_at))
    absence_reportable = (league.status == 'active' and match.round == league.current_round
        and match.effective_result_state() == 'void' and match.resolution_kind == 'player_unavailable'
        and user_id in (match.player1_id, match.player2_id))
    data['can_report_played_after_absence'] = bool(absence_reportable)
    if absence_reportable:
        data['can_report_result'] = True
    data['requires_explicit_confirmation'] = match.resolution_kind == 'absence_result_claim'
    if data['requires_explicit_confirmation']:
        data['review_deadline_at'] = None
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


def _league_schedule_payload(league, match, user_id):
    participant = user_id in (match.player1_id, match.player2_id)
    organizer = user_id == league.organizer_id
    active = (league.status == 'active' and match.round == league.current_round
              and match.effective_result_state() == 'unreported'
              and league.member_for(match.player1_id) and league.member_for(match.player2_id)
              and not is_blocked_between(match.player1_id, match.player2_id))
    options = json.loads(match.schedule_proposals or '[]')
    permitted = participant or organizer
    pending = bool(options and active)
    return {
        'schedule_version': int(match.schedule_version or 0),
        'schedule_status': 'waiting_reply' if pending else 'scheduled' if match.scheduled_at else 'needs_time',
        'scheduled_at': iso(match.scheduled_at) if permitted else None,
        'scheduled_court': match.scheduled_court.to_summary_dict() if permitted and match.scheduled_court else None,
        'scheduled_duration_minutes': match.scheduled_duration_minutes,
        'schedule_proposed_by_id': match.schedule_proposed_by_id if permitted else None,
        'schedule_options': options if permitted and pending else [],
        'can_propose_schedule': bool(active and participant),
        'can_respond_schedule': bool(active and participant and pending and match.schedule_proposed_by_id != user_id),
        'can_cancel_schedule': bool(active and permitted and (match.scheduled_at or pending)),
    }


def _schedule_context(league_id, match_id, payload):
    # Same player locks serialize accepted slots across different matches.
    match = db.session.get(LeagueMatch, match_id)
    if not match or match.league_id != league_id:
        return None, None, (jsonify({'error': 'match_not_found'}), 404)
    if g.current_user.id not in (match.player1_id, match.player2_id, match.league.organizer_id):
        return None, None, (jsonify({'error': 'league_schedule_forbidden'}), 403)
    User.query.filter(User.id.in_([match.player1_id, match.player2_id])).order_by(User.id).with_for_update().all()
    league = _locked_league(league_id)
    match = _locked_league_match(league_id, match_id)
    if (league.status != 'active' or match.round != league.current_round
            or match.effective_result_state() != 'unreported'):
        return None, None, (jsonify({'error': 'league_schedule_closed'}), 409)
    if (not league.member_for(match.player1_id) or not league.member_for(match.player2_id)
            or is_blocked_between(match.player1_id, match.player2_id)):
        return None, None, (jsonify({'error': 'league_schedule_unavailable'}), 409)
    version = payload.get('expected_schedule_version')
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        return None, None, (jsonify({'error': 'schedule_version_required'}), 400)
    if version != int(match.schedule_version or 0):
        return None, None, (jsonify({'error': 'stale_schedule', 'schedule_version': match.schedule_version}), 409)
    return league, match, None


def _schedule_overlap(match, start, duration):
    end = start + timedelta(minutes=duration)
    player_ids = [match.player1_id, match.player2_id]
    games = Game.query.join(GamePlayer).filter(
        GamePlayer.user_id.in_(player_ids), Game.status == 'upcoming',
        Game.scheduled_at < end, Game.scheduled_at >= start - timedelta(days=1),
    ).all()
    if any(game.scheduled_at + timedelta(minutes=game.duration_minutes or 60) > start for game in games):
        return True
    matches = LeagueMatch.query.join(League).filter(
        LeagueMatch.id != match.id, League.status == 'active',
        LeagueMatch.round == League.current_round,
        db.or_(LeagueMatch.player1_id.in_(player_ids), LeagueMatch.player2_id.in_(player_ids)),
        LeagueMatch.scheduled_at < end, LeagueMatch.scheduled_at >= start - timedelta(days=1),
    ).all()
    if any(item.scheduled_at + timedelta(minutes=item.scheduled_duration_minutes or 60) > start for item in matches):
        return True
    entries = db.session.query(TournamentEntry.id).filter(db.or_(
        TournamentEntry.player1_id.in_(player_ids), TournamentEntry.player2_id.in_(player_ids),
    ))
    brackets = TournamentMatch.query.join(Tournament).filter(
        Tournament.status.in_(['registration', 'active']),
        TournamentMatch.scheduled_at < end, TournamentMatch.scheduled_at >= start - timedelta(days=1),
        db.or_(TournamentMatch.entry1_id.in_(entries), TournamentMatch.entry2_id.in_(entries)),
        TournamentMatch.winner_entry_id.is_(None),
    ).all()
    return any(item.scheduled_at + timedelta(minutes=item.tournament.match_minutes or 30) > start for item in brackets)


def _save_league_schedule(league, match, payload, changes, title):
    version = payload['expected_schedule_version']
    changed = LeagueMatch.query.filter_by(id=match.id, schedule_version=version).update(
        {**changes, 'schedule_version': version + 1, 'updated_at': utcnow()}, synchronize_session=False,
    )
    if changed != 1:
        db.session.rollback()
        return jsonify({'error': 'stale_schedule'}), 409
    for user_id in {match.player1_id, match.player2_id} - {g.current_user.id}:
        notify(user_id, 'league_schedule', title, league.name, related_league_id=league.id,
               related_user_id=g.current_user.id, action_url=_result_action_url(league, match),
               unread_dedupe_key=f'league-schedule:{match.id}:{version + 1}')
    db.session.commit()
    db.session.expire(match)
    return jsonify(_decorate_league_match(league, match, match.to_dict(g.current_user.id), g.current_user.id))


@leagues_bp.post('/leagues/<int:league_id>/matches/<int:match_id>/schedule/proposals')
@rate_limit(40, 60)
@login_required
def propose_league_schedule(league_id, match_id):
    payload = _result_request_payload()
    league, match, error = _schedule_context(league_id, match_id, payload)
    if error:
        return error
    if g.current_user.id not in (match.player1_id, match.player2_id):
        return jsonify({'error': 'players_propose_schedule'}), 403
    raw = payload.get('options')
    if not isinstance(raw, list) or not 1 <= len(raw) <= 3:
        return jsonify({'error': 'choose_one_to_three_times'}), 400
    options = []
    seen = set()
    for index, choice in enumerate(raw):
        if not isinstance(choice, dict):
            return jsonify({'error': 'invalid_schedule_option'}), 400
        start = _parse_scheduled_at(choice.get('starts_at'))
        duration = choice.get('duration_minutes', 60)
        court_id = choice.get('court_id')
        if (not start or start <= utcnow() or isinstance(duration, bool) or not isinstance(duration, int)
                or not 15 <= duration <= 240 or isinstance(court_id, bool) or not isinstance(court_id, int)):
            return jsonify({'error': 'invalid_schedule_option'}), 400
        deadline = _round_deadline(league)
        if deadline and start + timedelta(minutes=duration) > deadline:
            return jsonify({'error': 'schedule_after_round_deadline'}), 400
        court = db.session.get(Court, court_id)
        if not court:
            return jsonify({'error': 'court_not_found'}), 404
        if (start, court_id) in seen:
            return jsonify({'error': 'duplicate_schedule_option'}), 400
        seen.add((start, court_id))
        options.append({'id': str(index + 1), 'starts_at': iso(start), 'court_id': court.id,
                        'court_name': court.name, 'duration_minutes': duration})
    conflict = schedule_batch_review_needed([
        {'user_ids': [match.player1_id, match.player2_id], 'start': _parse_scheduled_at(option['starts_at']),
         'duration_minutes': option['duration_minutes'], 'exclusions': {'exclude_league_match_id': match.id}}
        for option in options], payload, scope=f'league-proposals:{match.id}:{match.schedule_version}', viewer_id=g.current_user.id)
    if conflict:
        return jsonify(conflict), 409
    return _save_league_schedule(league, match, payload, {
        'schedule_proposals': json.dumps(options), 'schedule_proposed_by_id': g.current_user.id,
    }, 'Choose a time for your league match')


@leagues_bp.post('/leagues/<int:league_id>/matches/<int:match_id>/schedule/respond')
@rate_limit(40, 60)
@login_required
def respond_league_schedule(league_id, match_id):
    payload = _result_request_payload()
    league, match, error = _schedule_context(league_id, match_id, payload)
    if error:
        return error
    if g.current_user.id not in (match.player1_id, match.player2_id) or match.schedule_proposed_by_id == g.current_user.id:
        return jsonify({'error': 'opponent_must_accept'}), 403
    options = json.loads(match.schedule_proposals or '[]')
    if not options:
        return jsonify({'error': 'no_schedule_proposal'}), 409
    changes = {'schedule_proposals': '[]', 'schedule_proposed_by_id': None}
    if payload.get('action') == 'accept':
        selected = next((choice for choice in options if choice['id'] == str(payload.get('option_id'))), None)
        if not selected:
            return jsonify({'error': 'schedule_option_not_found'}), 400
        start = _parse_scheduled_at(selected['starts_at'])
        deadline = _round_deadline(league)
        if start <= utcnow() or (deadline and start + timedelta(minutes=selected['duration_minutes']) > deadline):
            return jsonify({'error': 'schedule_option_expired'}), 409
        conflict = schedule_review_needed([match.player1_id, match.player2_id], start, selected['duration_minutes'],
            payload, scope=f'league-accept:{match.id}:{match.schedule_version}', viewer_id=g.current_user.id,
            exclude_league_match_id=match.id)
        if conflict:
            return jsonify(conflict), 409
        changes.update(scheduled_at=start, scheduled_court_id=selected['court_id'],
                       scheduled_duration_minutes=selected['duration_minutes'],
                       schedule_day_reminded_at=None, schedule_hour_reminded_at=None)
        title = 'Your league match is scheduled'
    elif payload.get('action') == 'decline':
        title = 'Your opponent needs another time'
    else:
        return jsonify({'error': 'invalid_schedule_response'}), 400
    return _save_league_schedule(league, match, payload, changes, title)


@leagues_bp.post('/leagues/<int:league_id>/matches/<int:match_id>/schedule/cancel')
@rate_limit(40, 60)
@login_required
def cancel_league_schedule(league_id, match_id):
    payload = _result_request_payload()
    league, match, error = _schedule_context(league_id, match_id, payload)
    if error:
        return error
    return _save_league_schedule(league, match, payload, {
        'scheduled_at': None, 'scheduled_court_id': None,
        'schedule_proposals': '[]', 'schedule_proposed_by_id': None,
        'schedule_day_reminded_at': None, 'schedule_hour_reminded_at': None,
    }, 'Your league match needs a new time')


def league_agenda_payload(user_id):
    matches = LeagueMatch.query.join(League).filter(
        League.status == 'active', LeagueMatch.round == League.current_round,
        LeagueMatch.result_state.in_(['unreported', 'awaiting_confirmation', 'disputed', 'unresolved']),
        LeagueMatch.winner_id.is_(None),
        db.or_(LeagueMatch.player1_id == user_id, LeagueMatch.player2_id == user_id),
    ).all()
    items = []
    for match in matches:
        if not match.league.member_for(user_id) or is_blocked_between(match.player1_id, match.player2_id):
            continue
        item = _decorate_league_match(match.league, match, match.to_dict(user_id), user_id)
        items.append({**item, 'kind': 'league_match', 'league_id': match.league_id,
                      'league_name': match.league.name, 'action_url': _result_action_url(match.league, match),
                      'opponent': item['player2'] if match.player1_id == user_id else item['player1']})
    return {'items': sorted(items, key=lambda row: (row['scheduled_at'] is None, row['scheduled_at'] or '', row['id']))}


@leagues_bp.get('/leagues/agenda')
@login_required
def my_league_agenda():
    return jsonify(league_agenda_payload(g.current_user.id))


def send_league_schedule_reminders(now=None):
    """One reminder per accepted slot/window; changing the slot resets markers."""
    now = now or utcnow()
    ids = db.session.query(LeagueMatch.league_id, LeagueMatch.id).join(League).filter(
        League.status == 'active', LeagueMatch.round == League.current_round,
        LeagueMatch.result_state == 'unreported', LeagueMatch.winner_id.is_(None),
        LeagueMatch.scheduled_at > now, LeagueMatch.scheduled_at <= now + timedelta(hours=24),
        db.or_(LeagueMatch.schedule_day_reminded_at.is_(None),
               db.and_(LeagueMatch.scheduled_at <= now + timedelta(hours=1), LeagueMatch.schedule_hour_reminded_at.is_(None))),
    ).order_by(LeagueMatch.scheduled_at, LeagueMatch.id).limit(200).all()
    db.session.rollback()
    sent = 0
    for league_id, match_id in ids:
        league = _locked_league(league_id)
        match = _locked_league_match(league_id, match_id)
        if (not league or not match or league.status != 'active' or match.round != league.current_round
                or match.effective_result_state() != 'unreported' or not match.scheduled_at
                or match.scheduled_at <= now or not league.member_for(match.player1_id)
                or not league.member_for(match.player2_id) or is_blocked_between(match.player1_id, match.player2_id)):
            db.session.rollback()
            continue
        hourly = match.scheduled_at <= now + timedelta(hours=1)
        field = 'schedule_hour_reminded_at' if hourly else 'schedule_day_reminded_at'
        if getattr(match, field) is not None or match.scheduled_at > now + timedelta(hours=24):
            db.session.rollback()
            continue
        for player_id in [match.player1_id, match.player2_id]:
            notify(player_id, 'league_reminder', 'Your league match starts within an hour' if hourly else 'Your league match is within 24 hours',
                   f'{league.name} · {match.scheduled_court.name if match.scheduled_court else league.court.name}',
                   related_league_id=league.id, action_url=_result_action_url(league, match),
                   unread_dedupe_key=f'league-schedule:{match.id}:{match.scheduled_at.isoformat()}:{field}')
            sent += 1
        setattr(match, field, now)
        if hourly and match.schedule_day_reminded_at is None:
            match.schedule_day_reminded_at = now
        db.session.commit()
    return {'reminded': sent}


def _league_action_summary(league, user_id):
    closed_reviews = []
    for match in league.matches:
        review = json.loads(match.closed_round_review or '{}')
        if review.get('status') == 'pending' and (user_id == league.organizer_id or (
            user_id in (match.player1_id, match.player2_id) and user_id != review.get('requested_by_id')
            and not any(response.get('user_id') == user_id for response in review.get('responses', []))
        )):
            closed_reviews.append(match)
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
        match.id: match for match in closed_reviews + mine + organizer_matches + unplayed
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
        'closed_review_action_count': len(closed_reviews),
        'pending_action_count': len(action_matches) + int(start_action_pending),
        'action_match_id': ordered_actions[0].id if ordered_actions else None,
        'start_action_pending': start_action_pending,
    }


ROUND_STANDING_RULE = ('Only confirmed matches in this round count: 3 points for a win, '
    '1 for a played loss, 0 for not played. Ties use wins, then points scored minus points conceded. '
    'Players still tied share a place. Adjacent divisions swap a clear leader and clear last place '
    'only when both have played; ties or absences can prevent a swap. Season totals do not decide movement.')


def _round_tables(league, round_number=None, result_overrides=None):
    round_number = round_number or league.current_round
    members = {member.user_id: member for member in league.members}
    rows = {}
    current = [m for m in league.matches if m.round == round_number]
    for match in current:
        for user_id, player in ((match.player1_id, match.player1), (match.player2_id, match.player2)):
            rows.setdefault((match.box, user_id), {'user_id': user_id, 'box': match.box,
                'user': player.to_public_dict() if player else {'id': user_id, 'display_name': 'Former player'}, 'points': 0, 'wins': 0,
                'losses': 0, 'played': 0, 'point_difference': 0})
        state, winner, score1, score2 = (result_overrides or {}).get(match.id,
            (match.effective_result_state(), match.winner_id, match.score1, match.score2))
        if state != 'confirmed' or winner is None:
            continue
        for user_id, scored, conceded in ((match.player1_id, score1, score2), (match.player2_id, score2, score1)):
            row = rows[(match.box, user_id)]
            won = user_id == winner
            row['points'] += 3 if won else 1
            row['wins'] += int(won)
            row['losses'] += int(not won)
            row['played'] += 1
            row['point_difference'] += (scored or 0) - (conceded or 0)
    if round_number == league.current_round:
        for member in league.members:
            if member.box and not member.withdrawn_at:
                rows.setdefault((member.box, member.user_id), {'user_id': member.user_id, 'box': member.box,
                    'user': member.user.to_public_dict(), 'points': 0, 'wins': 0, 'losses': 0, 'played': 0, 'point_difference': 0})
    boxes = {}
    for row in rows.values():
        member = members.get(row['user_id'])
        row['unavailable'] = bool(member and member.unavailable_round == round_number)
        # Historic match identity survives older membership removal. It must
        # remain visible without making that former player eligible to move.
        row['withdrawing'] = not member or member.withdraw_after_round == round_number or bool(member.withdrawn_at)
        boxes.setdefault(row['box'], []).append(row)
    key = lambda row: (row['points'], row['wins'], row['point_difference'])
    for standing in boxes.values():
        standing.sort(key=lambda row: (-row['points'], -row['wins'], -row['point_difference'], row['user_id']))
        for index, row in enumerate(standing):
            row['tied'] = sum(key(other) == key(row) for other in standing) > 1
            row['place'] = 1 + sum(key(other) > key(row) for other in standing)
    return [{'box': number, 'players': boxes[number]} for number in sorted(boxes)]


def _round_close_preview(league, *, finish=False, now=None):
    current = [m for m in league.matches if m.round == league.current_round]
    tables = _round_tables(league)
    leaving = [m for m in league.members if not m.withdrawn_at and m.withdraw_after_round == league.current_round]
    remaining = [m for m in league.members if not m.withdrawn_at and m not in leaving]
    ends = bool(finish or (league.total_rounds and league.current_round >= league.total_rounds) or len(remaining) < 2)
    movements, notes = [], []
    if not ends:
        for upper, lower in zip(tables, tables[1:]):
            bottom, top = upper['players'][-1], lower['players'][0]
            if all(row['played'] and not row['tied'] and not row['unavailable'] and not row['withdrawing'] for row in (bottom, top)):
                for row, destination in ((bottom, lower['box']), (top, upper['box'])):
                    movements.append({'user_id': row['user_id'], 'name': row['user']['display_name'], 'from_box': row['box'], 'to_box': destination})
            else:
                notes.append(f"Divisions {upper['box']} and {lower['box']}: no swap while a deciding place is tied, unplayed, unavailable or withdrawing.")
        assignments = {m.user_id: m.box for m in remaining}
        assignments.update({move['user_id']: move['to_box'] for move in movements})
        while True:
            groups = {}
            for user_id, division in assignments.items():
                groups.setdefault(division, []).append(user_id)
            single = next((number for number in sorted(groups) if len(groups[number]) < 2), None)
            if single is None or len(groups) < 2:
                break
            neighbor = min((number for number in groups if number != single), key=lambda number: (abs(number-single), number))
            target = min(single, neighbor)
            for user_id in groups[single] + groups[neighbor]:
                assignments[user_id] = target
            notes.append(f'Divisions {single} and {neighbor} combine into Division {target} after withdrawals, so everyone has an opponent.')
        existing = {move['user_id']: move for move in movements}
        for member in remaining:
            destination = assignments[member.user_id]
            if destination != (existing.get(member.user_id) or {}).get('to_box', member.box):
                movements = [move for move in movements if move['user_id'] != member.user_id]
                if destination != member.box:
                    movements.append({'user_id': member.user_id, 'name': member.user.display_name, 'from_box': member.box, 'to_box': destination, 'reason': 'division_combined'})
    leader = tables[0]['players'][0] if tables and tables[0]['players'] else None
    champion_id = leader['user_id'] if leader and leader['played'] and not leader['tied'] else None
    state = {'round': league.current_round, 'version': int(league.round_version or 0), 'status': league.status,
        'total_rounds': league.total_rounds, 'round_days': league.round_days, 'finish': bool(finish),
        'matches': [(m.id, m.effective_result_state(), m.result_version, m.winner_id, m.score1, m.score2, m.schedule_version) for m in current],
        'members': [(m.user_id, m.box, m.unavailable_round, m.withdraw_after_round, iso(m.withdrawn_at)) for m in league.members]}
    return {'round': league.current_round, 'round_version': int(league.round_version or 0),
        'preview_fingerprint': hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest(),
        'round_standings': tables, 'standing_rule': ROUND_STANDING_RULE, 'movements': movements, 'movement_notes': notes,
        'unresolved_count': sum(m.effective_result_state() in UNRESOLVED_RESULT_STATES for m in current),
        'unplayed_count': sum(m.effective_result_state() == 'unreported' for m in current),
        'not_played_count': sum(m.effective_result_state() == 'void' for m in current),
        'withdrawals': [{'user_id': m.user_id, 'name': m.user.display_name} for m in leaving],
        'ends_season': ends, 'next_round': None if ends else league.current_round + 1,
        'next_deadline_at': None if ends else iso((now or utcnow()) + timedelta(days=league.round_days)),
        'champion_user_id': champion_id if ends else None,
        'champion_name': members_name if ends and champion_id and (members_name := league.member_for(champion_id).user.display_name) else None}


def _league_payload(league, user_id, *, detail=False, detail_match_id=None, personal_match_id=None):
    data = league.to_dict(
        user_id,
        detail=detail,
        detail_match_id=detail_match_id,
    )
    data.update(_league_action_summary(league, user_id))
    member = league.member_for(user_id)
    mine = [match for match in league.matches if member and not member.withdrawn_at
        and user_id in (match.player1_id, match.player2_id) and match.round == league.current_round
        and match.effective_result_state() not in ('confirmed', 'void')]
    mine.sort(key=lambda match: (match.effective_result_state() != 'awaiting_confirmation',
        match.scheduled_at is None, match.scheduled_at or league.starts_at, match.id))
    if personal_match_id is not None:
        mine = [match for match in mine if match.id == personal_match_id]
    data['personal_match'] = None
    if mine and league.status == 'active':
        match = mine[0]
        data['personal_match'] = {'id': match.id, 'round': match.round,
            'opponent': (match.player2 if match.player1_id == user_id else match.player1).display_name,
            'starts_at': iso(match.scheduled_at), 'timing': 'scheduled' if match.scheduled_at else 'needs_time',
            'court_name': match.scheduled_court.name if match.scheduled_court else '',
            'state': match.effective_result_state(), **_league_schedule_payload(league, match, user_id)}
    data['round_deadline_at'] = iso(_round_deadline(league))
    data['result_auto_confirm_hours'] = _league_result_window_hours()
    data['season_end_estimate_at'] = iso((_round_deadline(league) + timedelta(days=league.round_days * max(0, league.total_rounds - league.current_round))) if league.total_rounds and _round_deadline(league) else (league.starts_at + timedelta(days=league.round_days * league.total_rounds)) if league.total_rounds else None)
    if detail:
        data['round_standings'] = _round_tables(league)
        data['standing_rule'] = ROUND_STANDING_RULE
        data['round_history'] = json.loads(league.round_history or '[]')
        for event in data['round_history']:
            if event.get('action') == 'result_amended':
                amended = next((m for m in league.matches if m.id == event.get('match_id')), None)
                if not amended or user_id not in (league.organizer_id, amended.player1_id, amended.player2_id):
                    event.pop('reason', None)
        if league.status == 'active':
            preview = _round_close_preview(league)
            data['movement_preview'] = preview['movements']
            data['movement_notes'] = preview['movement_notes']
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


def _closed_review_eligible(league, match):
    return (league.status in ('active', 'completed') and
        (match.round < league.current_round or league.status == 'completed'))


def _closed_review_plan(league, match, review):
    voided = review['void']
    winner = None if voided else match.player1_id if review['score1'] > review['score2'] else match.player2_id
    overrides = {match.id: ('void' if voided else 'confirmed', winner, review['score1'], review['score2'])}
    tables = _round_tables(league, match.round, result_overrides=overrides)
    deltas = []
    for uid, player in ((match.player1_id, match.player1), (match.player2_id, match.player2)):
        old_win = int(match.winner_id == uid)
        old_loss = int(match.winner_id is not None and match.winner_id != uid)
        new_win, new_loss = int(winner == uid), int(winner is not None and winner != uid)
        member = league.member_for(uid)
        delta = {'points': 3*(new_win-old_win)+(new_loss-old_loss), 'wins': new_win-old_win, 'losses': new_loss-old_loss}
        deltas.append({'user_id': uid, 'name': player.display_name if player else 'Former player',
            'before': {key: getattr(member, key) for key in delta} if member else None,
            'after': {key: getattr(member, key)+value for key, value in delta.items()} if member else None,
            'delta': delta, 'membership_retained': bool(member)})
    champion_id = league.champion_user_id
    if league.status == 'completed' and match.round == league.current_round:
        leader = tables[0]['players'][0] if tables and tables[0]['players'] else None
        champion_id = leader['user_id'] if leader and leader['played'] and not leader['tied'] else None
    def name(uid):
        user = db.session.get(User, uid) if uid else None
        return user.display_name if user else None
    state = {'review': review, 'league_status': league.status, 'round_version': league.round_version,
        'round_history': league.round_history, 'current_round': league.current_round, 'champion': league.champion_user_id,
        'matches': [(m.id, m.round, m.result_version, m.winner_id, m.score1, m.score2, m.schedule_version, m.player1_id, m.player2_id) for m in league.matches],
        'members': [(m.user_id, m.box, m.points, m.wins, m.losses, iso(m.withdrawn_at)) for m in league.members]}
    return {'round': match.round, 'round_standings': tables, 'season_changes': deltas,
        'champion_before': {'id': league.champion_user_id, 'name': name(league.champion_user_id)},
        'champion_after': {'id': champion_id, 'name': name(champion_id)},
        'later_match_count': sum(m.round > match.round for m in league.matches),
        'preserved_appointments': sum(m.round > match.round and m.scheduled_at is not None for m in league.matches),
        'effect': 'The corrected result updates this round and the season record. Existing divisions, later matches and agreed appointments stay as drawn; past movement is not replayed.',
        'preview_fingerprint': hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()}


@leagues_bp.post('/leagues/<int:league_id>/matches/<int:match_id>/closed-review')
@rate_limit(30, 3600)
@login_required
def review_closed_league_result(league_id, match_id):
    league = _locked_league(league_id)
    if not league:
        return jsonify({'error': 'league_not_found'}), 404
    match = _locked_league_match(league.id, match_id)
    if not match:
        return jsonify({'error': 'match_not_found'}), 404
    actor = g.current_user.id
    organizer = actor == league.organizer_id
    participant = actor in (match.player1_id, match.player2_id)
    if not organizer and not participant:
        return jsonify({'error': 'players_or_organizer_only'}), 403
    if not _closed_review_eligible(league, match):
        return jsonify({'error': 'closed_round_required'}), 409
    payload = _result_request_payload()
    action = payload.get('action')
    if action not in ('request', 'respond', 'preview', 'approve', 'reject'):
        return jsonify({'error': 'invalid_action'}), 400
    version_error = _check_result_version(match, payload)
    if version_error:
        return version_error
    review = json.loads(match.closed_round_review or '{}')
    reason = str(payload.get('reason') or '').strip()[:500]
    if action == 'request':
        if review.get('status') == 'pending':
            return jsonify({'error': 'review_already_pending'}), 409
        if not reason:
            return jsonify({'error': 'reason_required'}), 400
        if not isinstance(payload.get('void', False), bool):
            return jsonify({'error': 'invalid_payload'}), 400
        voided = payload.get('void', False)
        if voided:
            score1, score2 = None, None
        else:
            scores, error = _parse_match_scores(payload)
            if error:
                return error
            score1, score2 = scores
        review = {'status': 'pending', 'requested_by_id': actor, 'requested_by_name': g.current_user.display_name,
            'requested_at': iso(utcnow()), 'reason': reason, 'void': voided, 'score1': score1, 'score2': score2,
            'original': {'state': match.effective_result_state(), 'score1': match.score1, 'score2': match.score2, 'winner_id': match.winner_id},
            'responses': []}
        event = 'late_review_requested'
    else:
        if review.get('status') != 'pending':
            return jsonify({'error': 'no_pending_review'}), 409
        if action == 'respond':
            if not participant or actor == review['requested_by_id']:
                return jsonify({'error': 'other_player_response_required'}), 403
            if not isinstance(payload.get('agree'), bool) or not reason:
                return jsonify({'error': 'agreement_and_reason_required'}), 400
            review['responses'].append({'user_id': actor, 'name': g.current_user.display_name,
                'agree': payload['agree'], 'reason': reason, 'at': iso(utcnow())})
            event = 'late_review_response'
        else:
            if not organizer:
                return jsonify({'error': 'organizer_only'}), 403
            plan = _closed_review_plan(league, match, review)
            if action == 'preview':
                return jsonify(plan)
            if not reason:
                return jsonify({'error': 'reason_required'}), 400
            if payload.get('preview_fingerprint') != plan['preview_fingerprint']:
                return jsonify({'error': 'preview_changed', 'message': 'Results or participants changed. Review the effects again.'}), 409
            if action == 'approve' and payload.get('acknowledge_downstream_effect') is not True:
                return jsonify({'error': 'downstream_acknowledgement_required'}), 400
            review.update(status='approved' if action == 'approve' else 'rejected', reviewed_by_id=actor,
                reviewed_at=iso(utcnow()), review_reason=reason)
            event = 'late_review_approved' if action == 'approve' else 'late_review_rejected'
            if action == 'approve':
                if review['void']:
                    _void_match_result(match)
                    match.result_state = 'void'
                else:
                    _finalize_match_score(match, review['score1'], review['score2'])
                    match.result_state = 'confirmed'
                match.confirmed_by_id, match.confirmed_at = actor, utcnow()
                match.resolution_kind = 'closed_round_amendment'
                if league.status == 'completed' and match.round == league.current_round:
                    league.champion_user_id = plan['champion_after']['id']
                history = json.loads(league.round_history or '[]')
                history.append({**plan, 'action': 'result_amended', 'match_id': match.id,
                    'amended_at': iso(utcnow()), 'reviewed_by_id': actor, 'reason': reason})
                league.round_history = json.dumps(history)
                league.round_version = int(league.round_version or 0)+1
    match.closed_round_review = json.dumps(review)
    version = _bump_result_version(match)
    CompetitionResultEvent.record('league', match.id, event, version, actor_id=actor,
        score1=review.get('score1'), score2=review.get('score2'), reason=reason)
    _notify_league_result_users(league, match, {match.player1_id, match.player2_id, league.organizer_id},
        f'{league.name}: closed-round result review',
        f"Round {match.round} · {review['status']}. " + ('Existing divisions and later appointments stay in place.' if action == 'approve' else 'Open the match to review the request and responses.'), actor_id=actor)
    return _commit_result_change(league.id, match.id)


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
    absence_claim = match.resolution_kind in ('player_unavailable', 'absence_result_claim')
    if match.effective_result_state() not in ('unreported', 'disputed') and not (
        absence_claim and match.effective_result_state() == 'void'
    ):
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
    match.resolution_kind = 'absence_result_claim' if absence_claim else ''
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
        reason=('Player reports that this match was played despite an absence notice. Explicit agreement or organizer review required.' if absence_claim else str(payload.get('reason') or '').strip()[:500]),
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
    if match.resolution_kind != 'absence_result_claim':
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
                if deadline and deadline <= now and match.resolution_kind != 'absence_result_claim':
                    scores, score_error = _parse_match_scores({
                        'score1': match.score1, 'score2': match.score2,
                    }, stored=True)
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
                elif (match.resolution_kind == 'absence_result_claim' and deadline and deadline <= now
                        and match.stall_alerted_at is None):
                    _notify_league_result_users(league, match, {league.organizer_id},
                        f'{league.name}: decide whether this match was played',
                        'A result was reported after an absence. It needs explicit agreement or an organizer decision.',
                        unread_dedupe_key=f'league-absence-review:{match.id}:{match.result_version}')
                    match.stall_alerted_at = now
                    outcomes['stalled'] += 1
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


def _do_advance(league, actor_id=None, now=None, finish=False):
    """Freeze a dated round, applying only its confirmed results to movement."""
    if _current_unresolved_matches(league, lock=True):
        return False
    now = now or utcnow()
    plan = _round_close_preview(league, finish=finish, now=now)
    history = json.loads(league.round_history or '[]')
    history.append({**plan, 'closed_at': iso(now), 'closed_by_id': actor_id})
    league.round_history = json.dumps(history)
    for match in league.matches:
        if match.round == league.current_round and match.effective_result_state() == 'unreported':
            match.result_state = 'void'
            match.resolution_kind = 'round_closed_unplayed'
            CompetitionResultEvent.record('league', match.id, 'voided', _bump_result_version(match), actor_id=actor_id, reason='Round closed without a reported result. No played loss or points recorded.')
    for move in plan['movements']:
        league.member_for(move['user_id']).box = move['to_box']
    for member in league.members:
        if member.withdraw_after_round == league.current_round and not member.withdrawn_at:
            member.withdrawn_at = now
    league.round_version = int(league.round_version or 0) + 1
    if plan['ends_season']:
        league.status = 'completed'
        league.completed_at = now
        league.champion = league.member_for(plan['champion_user_id']).user if plan['champion_user_id'] else None
        if league.champion:
            award_new_badges(league.champion)
    else:
        league.current_round += 1
        league.round_started_at = now
        league.round_deadline_override_at = None
        _generate_round(league)
    for member in league.members:
        if member.user_id != actor_id:
            title = (f'{league.name}: season complete' if plan['ends_season'] else
                f'{league.name}: your withdrawal is complete' if member.withdrawn_at else
                f'{league.name}: round {league.current_round} is ready · Division {member.box}')
            notify(
                member.user_id,
                'league_update',
                title,
                related_user_id=actor_id,
                related_league_id=league.id,
            )
    return True


@leagues_bp.get('/leagues/<int:league_id>/round/preview')
@login_required
def preview_league_round(league_id):
    league, err = _league_or_404(league_id)
    if err:
        return err
    if league.organizer_id != g.current_user.id:
        return jsonify({'error': 'organizer_only'}), 403
    if league.status != 'active':
        return jsonify({'error': 'not_active'}), 409
    return jsonify(_round_close_preview(league, finish=request.args.get('finish') == '1'))


@leagues_bp.post('/leagues/<int:league_id>/round/close')
@rate_limit(10, 3600)
@login_required
def close_league_round(league_id):
    return _reviewed_league_close(league_id)


def _reviewed_league_close(league_id, *, finish=None):
    league = _locked_league(league_id)
    if not league:
        return jsonify({'error': 'league_not_found'}), 404
    if league.organizer_id != g.current_user.id:
        return jsonify({'error': 'organizer_only'}), 403
    if league.status != 'active':
        return jsonify({'error': 'round_closed'}), 409
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict) or not isinstance(payload.get('finish', False), bool):
        return jsonify({'error': 'invalid_payload'}), 400
    finish = payload.get('finish', False) if finish is None else finish
    plan = _round_close_preview(league, finish=finish)
    if plan['unresolved_count']:
        return jsonify({'error': 'unresolved_results'}), 409
    if payload.get('preview_fingerprint') != plan['preview_fingerprint']:
        return jsonify({'error': 'preview_changed', 'message': 'Results or availability changed. Review this round again.'}), 409
    if not _do_advance(league, actor_id=g.current_user.id, finish=finish):
        return jsonify({'error': 'unresolved_results'}), 409
    db.session.commit()
    data = _league_payload(league, g.current_user.id, detail=True)
    champion = league.member_for(league.champion_user_id) if league.champion_user_id else None
    data['champion'] = champion.to_dict() if champion else None
    return jsonify(data)


@leagues_bp.post('/leagues/<int:league_id>/round/extend')
@rate_limit(15, 3600)
@login_required
def extend_league_round(league_id):
    league = _locked_league(league_id)
    if not league:
        return jsonify({'error': 'league_not_found'}), 404
    if league.organizer_id != g.current_user.id:
        return jsonify({'error': 'organizer_only'}), 403
    if league.status != 'active':
        return jsonify({'error': 'round_closed'}), 409
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'error': 'invalid_payload'}), 400
    deadline = _parse_scheduled_at(payload.get('deadline_at'))
    previous = _round_deadline(league)
    reason = str(payload.get('reason') or '').strip()[:300]
    if not deadline or not previous or deadline <= max(previous, utcnow()) or deadline > max(previous, utcnow()) + timedelta(days=28):
        return jsonify({'error': 'invalid_round_deadline', 'message': 'Choose a later deadline within the next 28 days.'}), 400
    if not reason:
        return jsonify({'error': 'reason_required'}), 400
    plan = _round_close_preview(league)
    fingerprint = hashlib.sha256((plan['preview_fingerprint'] + iso(deadline) + reason).encode()).hexdigest()
    if payload.get('preview') is True:
        return jsonify({'round': league.current_round, 'previous_deadline_at': iso(previous), 'deadline_at': iso(deadline),
            'preview_fingerprint': fingerprint, 'reason': reason,
            'retained_appointments': sum(m.round == league.current_round and m.scheduled_at is not None and m.effective_result_state() == 'unreported' for m in league.matches),
            'notification_count': sum(not m.withdrawn_at and m.user_id != g.current_user.id for m in league.members)})
    if payload.get('preview_fingerprint') != fingerprint:
        return jsonify({'error': 'preview_changed', 'message': 'The round changed. Review the extension again.'}), 409
    history = json.loads(league.round_history or '[]')
    history.append({'action': 'deadline_extended', 'round': league.current_round, 'at': iso(utcnow()),
        'actor_id': g.current_user.id, 'previous_deadline_at': iso(previous), 'deadline_at': iso(deadline), 'reason': reason})
    league.round_history = json.dumps(history)
    league.round_deadline_override_at = deadline
    league.round_version = int(league.round_version or 0) + 1
    league.deadline_alerted_round = 0
    for member in league.members:
        if member.withdrawn_at:
            continue
        member.reminded_round = 0
        if member.user_id != g.current_user.id:
            notify(member.user_id, 'league_update', f'{league.name}: round {league.current_round} deadline extended',
                f'New deadline: {iso(deadline)}. {reason}. Agreed match appointments are unchanged.',
                related_user_id=g.current_user.id, related_league_id=league.id,
                action_url=f'/#league/{league.id}')
    db.session.commit()
    return jsonify(_league_payload(league, g.current_user.id, detail=True))


@leagues_bp.post('/leagues/<int:league_id>/availability')
@rate_limit(30, 3600)
@login_required
def league_availability(league_id):
    league = _locked_league(league_id)
    if not league:
        return jsonify({'error': 'league_not_found'}), 404
    member = league.member_for(g.current_user.id)
    if not member or member.withdrawn_at:
        return jsonify({'error': 'active_member_only'}), 403
    if league.status != 'active':
        return jsonify({'error': 'round_closed'}), 409
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or payload.get('action') not in ('unavailable', 'available', 'withdraw', 'stay'):
        return jsonify({'error': 'invalid_action'}), 400
    action = payload['action']
    matches = [m for m in league.matches if m.round == league.current_round and member.user_id in (m.player1_id, m.player2_id)]
    affected = [m for m in matches if m.effective_result_state() == 'unreported'] if action == 'unavailable' else []
    plan = _round_close_preview(league)
    fingerprint = hashlib.sha256((plan['preview_fingerprint'] + str(member.user_id) + action).encode()).hexdigest()
    if payload.get('preview') is True:
        return jsonify({'preview_fingerprint': fingerprint, 'round': league.current_round,
            'action': action, 'affected_matches': [{'id': m.id, 'opponent': (m.player2 if m.player1_id == member.user_id else m.player1).display_name,
                'scheduled_at': iso(m.scheduled_at), 'court': m.scheduled_court.name if m.scheduled_court else None} for m in affected],
            'pending_result_count': sum(m.effective_result_state() in UNRESOLVED_RESULT_STATES for m in matches)})
    if payload.get('preview_fingerprint') != fingerprint:
        return jsonify({'error': 'preview_changed', 'message': 'Your matches changed. Review availability again.'}), 409
    already = (action == 'unavailable' and member.unavailable_round == league.current_round or
        action == 'available' and member.unavailable_round != league.current_round or
        action == 'withdraw' and member.withdraw_after_round == league.current_round or
        action == 'stay' and member.withdraw_after_round is None)
    if already:
        return jsonify(_league_payload(league, g.current_user.id, detail=True))
    history = json.loads(member.availability_history or '[]')
    event = {'action': action, 'round': league.current_round, 'at': iso(utcnow()), 'matches': []}
    if action in ('unavailable', 'available'):
        member.unavailable_round = league.current_round if action == 'unavailable' else None
        for match in matches:
            restore = (action == 'available' and match.resolution_kind == 'player_unavailable'
                and match.effective_result_state() == 'void'
                and all(league.member_for(uid) and not league.member_for(uid).withdrawn_at
                    and league.member_for(uid).unavailable_round != league.current_round
                    for uid in (match.player1_id, match.player2_id)))
            if match not in affected and not restore:
                continue
            event['matches'].append({'id': match.id, 'scheduled_at': iso(match.scheduled_at),
                'court_id': match.scheduled_court_id, 'proposals': json.loads(match.schedule_proposals or '[]'),
                'scheduled_duration_minutes': match.scheduled_duration_minutes, 'schedule_version': match.schedule_version})
            match.result_state = 'unreported' if restore else 'void'
            match.resolution_kind = '' if restore else 'player_unavailable'
            match.scheduled_at = None
            match.scheduled_court = None
            match.schedule_proposals = '[]'
            match.schedule_proposed_by_id = None
            match.schedule_version = int(match.schedule_version or 0) + 1
            match.schedule_day_reminded_at = match.schedule_hour_reminded_at = None
            reason = ('Player is available again. Agree a new time; the old appointment was not restored.' if restore else 'Player unavailable this round. Appointment cancelled; no played loss or points recorded.')
            CompetitionResultEvent.record('league', match.id, 'reopened' if restore else 'voided', _bump_result_version(match), actor_id=member.user_id, reason=reason)
            _notify_league_result_users(league, match, (match.player1_id, match.player2_id),
                f'{member.user.display_name}: ' + ('available to play again' if restore else 'unavailable this round'), reason, actor_id=member.user_id)
    else:
        member.withdraw_after_round = league.current_round if action == 'withdraw' else None
    history.append(event)
    member.availability_history = json.dumps(history)
    league.round_version = int(league.round_version or 0) + 1
    if league.organizer_id != member.user_id:
        notify(league.organizer_id, 'league_update', f'{member.user.display_name} updated their league availability',
            'Withdrawal after this round' if action == 'withdraw' else 'Staying in the season' if action == 'stay' else 'Unavailable this round' if action == 'unavailable' else 'Available again',
            related_user_id=member.user_id, related_league_id=league.id)
    db.session.commit()
    return jsonify(_league_payload(league, g.current_user.id, detail=True))


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
        deadline = _round_deadline(league)
        if now >= deadline:
            # Re-lock and refresh before closing so a concurrent report cannot
            # slip into the old round after the unresolved-result check.
            league = _locked_league(league.id)
            if not league or league.status != 'active' or not league.round_started_at:
                continue
            deadline = _round_deadline(league)
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
            # A deadline asks the organizer to review actual results and absences.
            # It cannot silently move players or close a season.
            notify(league.organizer_id, 'league_update',
                f'{league.name}: review round {league.current_round} before closing',
                'Review unplayed matches, movement and the next round before notifying players.',
                related_league_id=league.id, action_url=f'/#league/{league.id}',
                unread_dedupe_key=f'league-close-preview:{league.id}:{league.current_round}')
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
    return _reviewed_league_close(league_id, finish=False)


@leagues_bp.post('/leagues/<int:league_id>/complete')
@rate_limit(10, 3600)
@login_required
def complete_league(league_id):
    return _reviewed_league_close(league_id, finish=True)


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
