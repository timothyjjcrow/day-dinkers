"""Box leagues: rating-seeded boxes, round robin within each box, promotion/
relegation between rounds, champion crowned from box 1 at completion."""
from flask import Blueprint, g, jsonify, request
from sqlalchemy.exc import IntegrityError

from backend.app import db
from backend.models import (
    CompetitionResultEvent, Court, League, LeagueMatch, LeagueMember,
    is_blocked_between, notify, utcnow,
)
from backend.security import rate_limit

leagues_bp = Blueprint('leagues', __name__)

from backend.routes.auth import login_required  # noqa: E402
from backend.routes.games import _parse_scheduled_at  # noqa: E402

MIN_PLAYERS = 3


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
                f'{club.name}: new box league — {name}. Signups open',
                related_user_id=g.current_user.id,
                related_league_id=league.id,
            )
    db.session.commit()
    return jsonify(league.to_dict(g.current_user.id, detail=True)), 201


@leagues_bp.get('/leagues')
@login_required
def list_leagues():
    """Open and running leagues (newest start first), plus any of mine."""
    court_id = request.args.get('court_id', type=int)
    query = League.query.filter(League.status.in_(['registration', 'active']))
    if court_id:
        query = query.filter(League.court_id == court_id)
    leagues = query.order_by(League.starts_at.asc()).limit(50).all()
    mine_extra = [
        m.league for m in LeagueMember.query.filter_by(user_id=g.current_user.id)
        if m.league and m.league not in leagues
        and m.league.status != 'cancelled'  # keep completed for history, drop noise
        and (not court_id or m.league.court_id == court_id)
    ]
    return jsonify({
        'items': [
            lg.to_dict(g.current_user.id)
            for lg in leagues + sorted(mine_extra, key=lambda x: x.id, reverse=True)[:10]
        ],
    })


@leagues_bp.get('/leagues/<int:league_id>')
@login_required
def league_detail(league_id):
    league, err = _league_or_404(league_id)
    if err:
        return err
    requested_match_id = request.args.get('match_id', type=int)
    data = league.to_dict(
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
    return jsonify(data)


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
    return jsonify(league.to_dict(g.current_user.id, detail=True))


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
    return jsonify(league.to_dict(g.current_user.id, detail=True))


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
    return jsonify(match.to_dict(g.current_user.id))


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
        f'{league.name}: the organizer voided your match result'
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


def _do_advance(league, actor_id=None):
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
    league.round_started_at = utcnow()
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


def advance_due_league_rounds():
    """Lazy sweep (runs on /me reads, like tournament reminders): once a
    round's window has elapsed, close it exactly like the organizer button
    would — one round per sweep so a dormant league doesn't fast-forward.
    Also nags members who still have unplayed matches when the round enters
    its final two days (once per round)."""
    from datetime import timedelta
    now = utcnow()
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
            _do_advance(league, actor_id=None)
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
    return jsonify(league.to_dict(g.current_user.id, detail=True))


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
    db.session.commit()
    data = league.to_dict(g.current_user.id, detail=True)
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
    from backend.models import LeagueChatRead, Message
    league, err = _league_or_404(league_id)
    if err:
        return err
    if not league.member_for(g.current_user.id):
        return jsonify({'error': 'members_only'}), 403
    since_id = request.args.get('since_id', type=int)
    query = Message.query.filter(Message.league_id == league_id)
    from backend.routes.chat import chat_messages_page
    messages, has_more = chat_messages_page(query, since_id)

    # Reading the room marks it read — powers the league-screen badge.
    latest_id = messages[-1].id if since_id and has_more else (
        db.session.query(db.func.max(Message.id)).filter(
            Message.league_id == league_id,
        ).scalar() or 0
    )
    marker = LeagueChatRead.query.filter_by(
        user_id=g.current_user.id, league_id=league.id,
    ).first()
    if not marker:
        db.session.add(LeagueChatRead(
            user_id=g.current_user.id, league_id=league.id,
            last_read_message_id=latest_id,
        ))
        db.session.commit()
    elif latest_id > marker.last_read_message_id:
        marker.last_read_message_id = latest_id
        db.session.commit()

    from backend.routes.chat import room_heart_counts
    return jsonify({
        'league': {'id': league.id, 'name': league.name},
        'items': [m.to_dict() for m in messages],
        'heart_counts': room_heart_counts('league_id', league_id),
        'has_more': has_more,
    })


@leagues_bp.post('/leagues/<int:league_id>/chat')
@rate_limit(60, 60)
@login_required
def send_league_message(league_id):
    from backend.models import Notification
    league, err = _league_or_404(league_id)
    if err:
        return err
    if not league.member_for(g.current_user.id):
        return jsonify({'error': 'members_only'}), 403
    from backend.routes.chat import prepare_chat_message
    message, replayed, body, err = prepare_chat_message(
        request.get_json(silent=True), g.current_user.id, league_id=league.id,
    )
    if err:
        return err
    if replayed:
        return jsonify(message.to_dict()), 200

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
    return jsonify(message.to_dict()), 201
