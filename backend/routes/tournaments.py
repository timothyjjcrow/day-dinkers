"""Tournaments: registration, seeded brackets, score reporting, standings."""
from datetime import timedelta

from flask import Blueprint, g, jsonify, request
from sqlalchemy.exc import IntegrityError

from backend.app import db
from backend.models import (
    CompetitionResultEvent,
    Court,
    TOURNAMENT_EVENT_TYPES,
    TOURNAMENT_FORMATS,
    Tournament,
    TournamentEntry,
    TournamentMatch,
    User,
    is_blocked_between,
    notify,
    utcnow,
)
from backend.routes.auth import login_required
from backend.routes.courts import haversine_miles
from backend.routes.games import _parse_scheduled_at
from backend.routes.social import friend_ids
from backend.security import rate_limit

tournaments_bp = Blueprint('tournaments', __name__)

MIN_ENTRIES = 2
MAX_ENTRIES_CAP = 32


def _seed_slot_order(size):
    """Bracket slot order for `size` seeds so 1 and 2 can only meet in the
    final: e.g. size 8 -> [1, 8, 4, 5, 2, 7, 3, 6]."""
    order = [1]
    while len(order) < size:
        doubled = len(order) * 2
        order = [s for seed in order for s in (seed, doubled + 1 - seed)]
    return order


def _round_robin_rounds(entry_ids):
    """Circle-method schedule: list of rounds, each a list of (a, b) pairs."""
    ids = list(entry_ids)
    if len(ids) % 2:
        ids.append(None)  # bye slot
    count = len(ids)
    rounds = []
    for _ in range(count - 1):
        pairs = [
            (ids[i], ids[count - 1 - i])
            for i in range(count // 2)
            if ids[i] is not None and ids[count - 1 - i] is not None
        ]
        rounds.append(pairs)
        ids = [ids[0], ids[-1]] + ids[1:-1]
    return rounds


def _notify_entry(entry, kind, title, body='', related_user_id=None,
                  action_url=''):
    for player in entry.players():
        notify(
            player.id, kind, title, body,
            related_user_id=related_user_id,
            related_tournament_id=entry.tournament_id,
            action_url=action_url,
        )


def _match_action_url(tournament, match):
    return f'/#tournament/{tournament.id}/match/{match.id}'


def _match_entries(tournament, match):
    entries = {entry.id: entry for entry in tournament.entries}
    return entries.get(match.entry1_id), entries.get(match.entry2_id)


def _entry_has_user(entry, user_id):
    return bool(
        entry and user_id is not None
        and user_id in (entry.player1_id, entry.player2_id)
    )


def _match_participant_ids(tournament, match):
    entry1, entry2 = _match_entries(tournament, match)
    return {
        player.id
        for entry in (entry1, entry2) if entry
        for player in entry.players()
    }


def _reporter_entry_id(tournament, match):
    entry1, entry2 = _match_entries(tournament, match)
    for entry in (entry1, entry2):
        if _entry_has_user(entry, match.reported_by_id):
            return entry.id
    return None


def _eligible_result_confirmer(tournament, match, user_id):
    """Only a match participant independent of the reporter may review.

    A participant reporter's teammates cannot confirm their own entry. When a
    neutral organizer reports, either competing entry can independently review.
    """
    if not user_id or user_id == match.reported_by_id:
        return False
    entry1, entry2 = _match_entries(tournament, match)
    viewer_entry = next(
        (entry for entry in (entry1, entry2) if _entry_has_user(entry, user_id)),
        None,
    )
    if not viewer_entry:
        return False
    reporter_entry_id = _reporter_entry_id(tournament, match)
    return reporter_entry_id is None or viewer_entry.id != reporter_entry_id


def _result_json_payload():
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def _parse_expected_result_version(payload):
    supplied = [
        payload[key] for key in (
            'expected_result_version', 'result_version', 'expected_version',
        ) if key in payload
    ]
    if not supplied:
        return None, None
    if any(isinstance(value, bool) for value in supplied):
        return None, 'invalid_result_version'
    if any(isinstance(value, float) and not value.is_integer() for value in supplied):
        return None, 'invalid_result_version'
    try:
        versions = [int(value) for value in supplied]
    except (TypeError, ValueError):
        return None, 'invalid_result_version'
    if any(version < 0 for version in versions) or len(set(versions)) != 1:
        return None, 'invalid_result_version'
    return versions[0], None


def _parse_score_pair(payload, stored_match=None):
    has_score1, has_score2 = 'score1' in payload, 'score2' in payload
    if not has_score1 and not has_score2 and stored_match is not None:
        raw1, raw2 = stored_match.score1, stored_match.score2
    elif has_score1 and has_score2:
        raw1, raw2 = payload.get('score1'), payload.get('score2')
    else:
        return None
    if isinstance(raw1, bool) or isinstance(raw2, bool):
        return None
    if isinstance(raw1, float) and not raw1.is_integer():
        return None
    if isinstance(raw2, float) and not raw2.is_integer():
        return None
    try:
        score1, score2 = int(raw1), int(raw2)
    except (TypeError, ValueError):
        return None
    if not (0 <= score1 <= 99 and 0 <= score2 <= 99) or score1 == score2:
        return None
    return score1, score2


def _record_result_action(match, action, actor_id=None, reason=''):
    match.result_version = int(match.result_version or 0) + 1
    CompetitionResultEvent.record(
        'tournament', match.id, action, match.result_version,
        actor_id=actor_id,
        score1=match.score1,
        score2=match.score2,
        reason=reason,
    )


def _locked_tournament_match(tournament_id, match_id):
    # Lock the tournament first so finalization/ELO remains serialized even when
    # two different matches are acted on at the same time. The per-match lock is
    # the narrower evidence/version guard on databases that support FOR UPDATE.
    tournament = (
        Tournament.query
        .filter(Tournament.id == tournament_id)
        .with_for_update()
        .first()
    )
    if not tournament:
        return None, None
    match = (
        TournamentMatch.query
        .filter(
            TournamentMatch.id == match_id,
            TournamentMatch.tournament_id == tournament_id,
        )
        .populate_existing()
        .with_for_update()
        .first()
    )
    return tournament, match


def _stale_result_response(tournament, match):
    data = _detail_payload(tournament, g.current_user.id)
    data['error'] = 'stale_result'
    data['current_result_version'] = int(match.result_version or 0)
    return jsonify(data), 409


def _commit_result_change(tournament_id, match_id):
    try:
        db.session.commit()
    except IntegrityError:
        # The immutable event's unique (type, match, version) key is the SQLite
        # fallback when row-level locks are unavailable.
        db.session.rollback()
        tournament = db.session.get(Tournament, tournament_id)
        match = db.session.get(TournamentMatch, match_id)
        if tournament and match and match.tournament_id == tournament.id:
            return _stale_result_response(tournament, match)
        return jsonify({'error': 'stale_result'}), 409
    tournament = db.session.get(Tournament, tournament_id)
    return jsonify(_detail_payload(tournament, g.current_user.id))


def _notify_result_users(tournament, match, user_ids, title, body='', actor_id=None):
    for user_id in set(user_ids) - ({actor_id} if actor_id else set()):
        notify(
            user_id,
            'tournament_score',
            title,
            body,
            related_user_id=actor_id,
            related_tournament_id=tournament.id,
            action_url=_match_action_url(tournament, match),
        )


def _standings(tournament):
    """Round-robin table: wins, then point diff, then points for."""
    rows = {
        e.id: {'entry': e.to_dict(), 'wins': 0, 'losses': 0, 'points_for': 0,
               'points_against': 0}
        for e in tournament.entries
    }
    for m in tournament.matches:
        if m.winner_entry_id is None or m.score1 is None:
            continue
        for eid, mine, theirs in (
            (m.entry1_id, m.score1, m.score2),
            (m.entry2_id, m.score2, m.score1),
        ):
            row = rows.get(eid)
            if not row:
                continue
            row['points_for'] += mine
            row['points_against'] += theirs
            if eid == m.winner_entry_id:
                row['wins'] += 1
            else:
                row['losses'] += 1
    table = sorted(
        rows.values(),
        key=lambda r: (
            -r['wins'],
            -(r['points_for'] - r['points_against']),
            -r['points_for'],
        ),
    )
    for row in table:
        row['point_diff'] = row['points_for'] - row['points_against']
    return table


def _detail_payload(tournament, user_id):
    data = tournament.to_dict(user_id, detail=True)
    matches = {match.id: match for match in tournament.matches}
    for item in data.get('matches', []):
        match = matches.get(item.get('id'))
        if not match:
            continue
        active = tournament.status == 'active'
        eligible = active and match.effective_result_state() == 'awaiting_confirmation' \
            and _eligible_result_confirmer(tournament, match, user_id)
        organizer = bool(user_id and user_id == tournament.organizer_id)
        item['awaiting_your_confirmation'] = bool(eligible)
        item['can_confirm_result'] = bool(eligible)
        item['can_dispute_result'] = bool(eligible)
        item['can_resolve_result'] = bool(
            active and organizer
            and match.effective_result_state() in ('awaiting_confirmation', 'disputed')
        )
        item['can_correct_result'] = bool(
            active and organizer
            and match.effective_result_state() == 'confirmed'
            and _confirmed_correction_is_safe(tournament, match)
        )
    if tournament.format == 'round_robin':
        data['standings'] = _standings(tournament)
    # Chat unread badge for members (no marker yet = everything is unread).
    data['chat_unread'] = 0
    if user_id and (user_id == tournament.organizer_id
                    or user_id in tournament.participant_ids()):
        from backend.models import Message, TournamentChatRead
        marker = TournamentChatRead.query.filter_by(
            user_id=user_id, tournament_id=tournament.id,
        ).first()
        data['chat_unread'] = Message.query.filter(
            Message.tournament_id == tournament.id,
            Message.id > (marker.last_read_message_id if marker else 0),
            Message.sender_id != user_id,
        ).count()
    return data


@tournaments_bp.get('/tournaments')
@login_required
def list_tournaments():
    user_id = g.current_user.id
    if request.args.get('mine'):
        entered = (
            db.session.query(TournamentEntry.tournament_id)
            .filter(db.or_(
                TournamentEntry.player1_id == user_id,
                TournamentEntry.player2_id == user_id,
            ))
        )
        rows = (
            Tournament.query
            .filter(db.or_(
                Tournament.organizer_id == user_id,
                Tournament.id.in_(entered),
            ))
            .filter(Tournament.status != 'cancelled')
            .order_by(Tournament.starts_at.desc())
            .limit(30)
            .all()
        )
        return jsonify({'items': [t.to_dict(user_id) for t in rows]})

    try:
        lat, lng = float(request.args.get('lat')), float(request.args.get('lng'))
        radius = min(float(request.args.get('radius', 60)), 250)
    except (TypeError, ValueError):
        return jsonify({'error': 'lat_lng_required'}), 400
    rows = (
        Tournament.query.join(Court)
        .filter(
            Tournament.status.in_(['registration', 'active']),
            Tournament.starts_at >= utcnow() - timedelta(days=3),
            Court.latitude.between(lat - 2.5, lat + 2.5),
            Court.longitude.between(lng - 2.5, lng + 2.5),
        )
        .order_by(Tournament.starts_at.asc())
        .limit(60)
        .all()
    )
    items = [
        t.to_dict(user_id) for t in rows
        if t.court and t.court.latitude is not None
        and haversine_miles(lat, lng, t.court.latitude, t.court.longitude) <= radius
    ][:30]
    return jsonify({'items': items})


@tournaments_bp.post('/tournaments')
@rate_limit(10, 3600)
@login_required
def create_tournament():
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
    if starts_at < utcnow() - timedelta(minutes=15):
        return jsonify({'error': 'scheduled_in_past'}), 400

    fmt = str(payload.get('format') or 'single_elim').strip().lower()
    if fmt not in TOURNAMENT_FORMATS:
        return jsonify({'error': 'invalid_format'}), 400
    event_type = str(payload.get('event_type') or 'singles').strip().lower()
    if event_type not in TOURNAMENT_EVENT_TYPES:
        return jsonify({'error': 'invalid_event_type'}), 400

    try:
        max_entries = int(payload.get('max_entries') or 8)
    except (TypeError, ValueError):
        max_entries = 8
    max_entries = min(max(max_entries, MIN_ENTRIES), MAX_ENTRIES_CAP)

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

    tournament = Tournament(
        name=name,
        description=str(payload.get('description') or '').strip()[:500],
        court_id=court.id,
        organizer_id=g.current_user.id,
        club=club,
        starts_at=starts_at,
        format=fmt,
        event_type=event_type,
        max_entries=max_entries,
        ranked=bool(payload.get('ranked')),
    )
    db.session.add(tournament)
    db.session.flush()

    # Club members hear about their club's tournaments first-class.
    club_pinged = set()
    if club:
        for member in club.members:
            if member.user_id == g.current_user.id:
                continue
            if is_blocked_between(g.current_user.id, member.user_id):
                continue
            notify(
                member.user_id,
                'club_game',
                f'{club.name}: new tournament — {name}',
                related_user_id=g.current_user.id,
                related_tournament_id=tournament.id,
            )
            club_pinged.add(member.user_id)

    # Ping players who saved this court — same opt-in, mute preference, and
    # 3h anti-churn window as new-game pings (shared court_game kind).
    from backend.models import FavoriteCourt, Notification
    fans = FavoriteCourt.query.filter_by(court_id=court.id).limit(200).all()
    recently_pinged = {
        n.user_id
        for n in Notification.query.filter(
            Notification.kind == 'court_game',
            Notification.related_user_id == g.current_user.id,
            Notification.created_at >= utcnow() - timedelta(hours=3),
            Notification.user_id.in_([f.user_id for f in fans]),
        )
    } if fans else set()
    for fan in fans:
        if fan.user_id == g.current_user.id or fan.user_id in recently_pinged:
            continue
        if fan.user_id in club_pinged:
            continue
        if is_blocked_between(g.current_user.id, fan.user_id):
            continue
        notify(
            fan.user_id,
            'court_game',
            f'New tournament at {court.name} — a court you saved',
            related_user_id=g.current_user.id,
            related_tournament_id=tournament.id,
        )
    db.session.commit()
    return jsonify(tournament.to_dict(g.current_user.id, detail=True)), 201


@tournaments_bp.patch('/tournaments/<int:tournament_id>')
@rate_limit(30, 3600)
@login_required
def edit_tournament(tournament_id):
    """Organizer tweaks: rename, description, reschedule, resize. Resizing is
    registration-only; the rest works until the tournament finishes."""
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'tournament_not_found'}), 404
    if tournament.organizer_id != g.current_user.id:
        return jsonify({'error': 'not_organizer'}), 403
    if tournament.status in ('completed', 'cancelled'):
        return jsonify({'error': 'already_finished'}), 409

    payload = request.get_json(silent=True) or {}

    if 'name' in payload:
        name = str(payload.get('name') or '').strip()[:120]
        if len(name) < 3:
            return jsonify({'error': 'name_required'}), 400
        tournament.name = name

    if 'description' in payload:
        tournament.description = str(payload.get('description') or '').strip()[:500]

    if 'max_entries' in payload:
        if tournament.status != 'registration':
            return jsonify({'error': 'registration_closed'}), 409
        try:
            max_entries = int(payload.get('max_entries'))
        except (TypeError, ValueError):
            return jsonify({'error': 'invalid_max_entries'}), 400
        max_entries = min(max(max_entries, MIN_ENTRIES), MAX_ENTRIES_CAP)
        if max_entries < len(tournament.entries):
            return jsonify({'error': 'below_entry_count'}), 400
        tournament.max_entries = max_entries

    rescheduled = False
    if 'starts_at' in payload:
        starts_at = _parse_scheduled_at(payload.get('starts_at'))
        if not starts_at:
            return jsonify({'error': 'invalid_starts_at'}), 400
        if starts_at < utcnow() - timedelta(minutes=15):
            return jsonify({'error': 'scheduled_in_past'}), 400
        rescheduled = starts_at != tournament.starts_at
        tournament.starts_at = starts_at
        if rescheduled:
            # Re-arm both reminders for the new time.
            tournament.reminded_at = None
            tournament.day_reminded_at = None

    if rescheduled:
        for uid in tournament.participant_ids() - {g.current_user.id}:
            notify(
                uid,
                'tournament_update',
                f'{tournament.name} was rescheduled by the organizer',
                related_user_id=g.current_user.id,
                related_tournament_id=tournament.id,
            )
    db.session.commit()
    return jsonify(_detail_payload(tournament, g.current_user.id))


@tournaments_bp.get('/tournaments/<int:tournament_id>')
@login_required
def tournament_detail(tournament_id):
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'tournament_not_found'}), 404
    return jsonify(_detail_payload(tournament, g.current_user.id))


@tournaments_bp.post('/tournaments/<int:tournament_id>/register')
@rate_limit(30, 3600)
@login_required
def register_entry(tournament_id):
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'tournament_not_found'}), 404
    if tournament.status != 'registration':
        return jsonify({'error': 'registration_closed'}), 409
    if len(tournament.entries) >= tournament.max_entries:
        return jsonify({'error': 'tournament_full'}), 409

    user = g.current_user
    if tournament.entry_for(user.id):
        return jsonify({'error': 'already_registered'}), 409

    partner = None
    if tournament.event_type == 'doubles':
        payload = request.get_json(silent=True) or {}
        try:
            partner_id = int(payload.get('partner_id') or 0)
        except (TypeError, ValueError):
            partner_id = 0
        partner = db.session.get(User, partner_id) if partner_id else None
        if not partner or partner.deleted_at or partner.id == user.id:
            return jsonify({'error': 'partner_required'}), 400
        # Doubles partners must be friends — you can't draft strangers.
        if partner.id not in friend_ids(user.id):
            return jsonify({'error': 'partner_not_friend'}), 403
        if is_blocked_between(user.id, partner.id):
            return jsonify({'error': 'blocked'}), 403
        if tournament.entry_for(partner.id):
            return jsonify({'error': 'partner_already_registered'}), 409

    # Assign the relationship (not the FK) so the tournament's loaded entries
    # collection includes this row when we serialize it below.
    entry = TournamentEntry(
        tournament=tournament,
        player1_id=user.id,
        player2_id=partner.id if partner else None,
    )
    db.session.add(entry)
    entry_name = f'{user.display_name} & {partner.display_name}' if partner \
        else user.display_name
    if partner:
        notify(
            partner.id,
            'tournament_invite',
            f'{user.display_name} signed you up as their partner for {tournament.name}',
            related_user_id=user.id,
            related_tournament_id=tournament.id,
        )
    if tournament.organizer_id != user.id:
        notify(
            tournament.organizer_id,
            'tournament_join',
            f'{entry_name} entered {tournament.name}',
            related_user_id=user.id,
            related_tournament_id=tournament.id,
        )
    db.session.commit()
    return jsonify(_detail_payload(tournament, user.id)), 201


@tournaments_bp.patch('/tournaments/<int:tournament_id>/register')
@rate_limit(30, 3600)
@login_required
def swap_partner(tournament_id):
    """The player who registered a doubles team swaps in a different partner
    while registration is still open."""
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'tournament_not_found'}), 404
    if tournament.status != 'registration':
        return jsonify({'error': 'registration_closed'}), 409
    if tournament.event_type != 'doubles':
        return jsonify({'error': 'not_doubles'}), 400
    entry = tournament.entry_for(g.current_user.id)
    if not entry:
        return jsonify({'error': 'not_registered'}), 404
    if entry.player1_id != g.current_user.id:
        return jsonify({'error': 'not_entry_owner'}), 403

    payload = request.get_json(silent=True) or {}
    try:
        partner_id = int(payload.get('partner_id') or 0)
    except (TypeError, ValueError):
        partner_id = 0
    partner = db.session.get(User, partner_id) if partner_id else None
    if not partner or partner.deleted_at or partner.id == g.current_user.id:
        return jsonify({'error': 'partner_required'}), 400
    if partner.id == entry.player2_id:
        return jsonify(_detail_payload(tournament, g.current_user.id))
    if partner.id not in friend_ids(g.current_user.id):
        return jsonify({'error': 'partner_not_friend'}), 403
    if is_blocked_between(g.current_user.id, partner.id):
        return jsonify({'error': 'blocked'}), 403
    if tournament.entry_for(partner.id):
        return jsonify({'error': 'partner_already_registered'}), 409

    old_partner_id = entry.player2_id
    entry.player2_id = partner.id
    # Refresh the relationship so this response serializes the new pair.
    entry.player2 = partner
    if old_partner_id:
        notify(
            old_partner_id,
            'tournament_withdraw',
            f'{g.current_user.display_name} changed partners for {tournament.name}',
            related_user_id=g.current_user.id,
            related_tournament_id=tournament.id,
        )
    notify(
        partner.id,
        'tournament_invite',
        f'{g.current_user.display_name} signed you up as their partner for {tournament.name}',
        related_user_id=g.current_user.id,
        related_tournament_id=tournament.id,
    )
    db.session.commit()
    return jsonify(_detail_payload(tournament, g.current_user.id))


@tournaments_bp.delete('/tournaments/<int:tournament_id>/register')
@rate_limit(30, 3600)
@login_required
def withdraw_entry(tournament_id):
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'tournament_not_found'}), 404
    if tournament.status != 'registration':
        return jsonify({'error': 'registration_closed'}), 409
    entry = tournament.entry_for(g.current_user.id)
    if not entry:
        return jsonify({'error': 'not_registered'}), 404
    other = next(
        (p for p in entry.players() if p.id != g.current_user.id), None,
    )
    if other:
        notify(
            other.id,
            'tournament_withdraw',
            f'{g.current_user.display_name} withdrew your team from {tournament.name}',
            related_user_id=g.current_user.id,
            related_tournament_id=tournament.id,
        )
    # Remove via the collection so delete-orphan fires AND the serialized
    # entries list below is already up to date.
    tournament.entries.remove(entry)
    db.session.commit()
    return jsonify(_detail_payload(tournament, g.current_user.id))


@tournaments_bp.delete('/tournaments/<int:tournament_id>/entries/<int:entry_id>')
@rate_limit(60, 3600)
@login_required
def remove_entry(tournament_id, entry_id):
    """Organizer removes an entry during registration."""
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'tournament_not_found'}), 404
    if tournament.organizer_id != g.current_user.id:
        return jsonify({'error': 'not_organizer'}), 403
    if tournament.status != 'registration':
        return jsonify({'error': 'registration_closed'}), 409
    entry = next((e for e in tournament.entries if e.id == entry_id), None)
    if not entry:
        return jsonify({'error': 'entry_not_found'}), 404
    _notify_entry(
        entry, 'tournament_withdraw',
        f'The organizer removed your entry from {tournament.name}',
        related_user_id=g.current_user.id,
    )
    tournament.entries.remove(entry)
    db.session.commit()
    return jsonify(_detail_payload(tournament, g.current_user.id))


def _propagate_bye_wins(tournament, notify_ready=False):
    """Auto-advance any undecided match with exactly one entry whose feeders
    are all settled (only possible via byes). Loops until stable."""
    total = tournament.total_rounds()
    changed = True
    while changed:
        changed = False
        for match in tournament.matches:
            if match.winner_entry_id is not None:
                continue
            has1, has2 = match.entry1_id is not None, match.entry2_id is not None
            if has1 == has2:  # 0 or 2 entries — nothing to auto-advance
                continue
            if not _feeders_settled(tournament, match):
                continue
            if match.id is None:
                db.session.flush()
            winner_id = match.entry1_id if has1 else match.entry2_id
            winner = next((e for e in tournament.entries if e.id == winner_id), None)
            match.winner_entry_id = winner_id
            match.winner_entry = winner
            match.result_state = 'bye'
            match.resolution_kind = 'automatic_bye'
            _record_result_action(
                match, 'resolved', reason='Automatic bye advancement',
            )
            _advance_winner(tournament, match, total, notify_ready=notify_ready)
            changed = True


def _feeders_settled(tournament, match):
    """True when every feeder match of this slot has produced its winner (or
    the slot never had a feeder, i.e. it's a round-1 bye)."""
    if match.round == 1:
        return True
    # The last-round position-1 slot is the 3rd-place match. Both semifinals
    # feed it their losers, so it must not be treated like an ordinary bracket
    # slot (whose position would otherwise produce an empty feeder set and make
    # ``all([])`` auto-advance the first loser as a bye).
    if (
        tournament.format == 'single_elim'
        and match.round == tournament.total_rounds()
        and match.position == 1
    ):
        feeders = [
            candidate for candidate in tournament.matches
            if candidate.round == match.round - 1
            and candidate.position in (0, 1)
        ]
        return len(feeders) == 2 and all(
            candidate.winner_entry_id is not None for candidate in feeders
        )
    feeders = [
        m for m in tournament.matches
        if m.round == match.round - 1 and m.position // 2 == match.position
    ]
    return all(m.winner_entry_id is not None for m in feeders)


def _final_and_third(tournament):
    """The championship match (position 0) and, when the bracket has one,
    the 3rd-place match (position 1) — both live in the last round."""
    total = tournament.total_rounds()
    final = third = None
    for m in tournament.matches:
        if m.round != total:
            continue
        if m.position == 0:
            final = m
        elif m.position == 1:
            third = m
    return final, third


def _result_downstream_matches(tournament, match):
    if tournament.format != 'single_elim':
        return []
    total = tournament.total_rounds()
    downstream = []
    if match.round < total:
        nxt = next(
            (
                candidate for candidate in tournament.matches
                if candidate.round == match.round + 1
                and candidate.position == match.position // 2
            ),
            None,
        )
        if nxt:
            downstream.append(nxt)
    if match.round == total - 1:
        _, third = _final_and_third(tournament)
        if third and third not in downstream:
            downstream.append(third)
    return downstream


def _confirmed_correction_is_safe(tournament, match):
    return all(
        downstream.effective_result_state() == 'unreported'
        and downstream.winner_entry_id is None
        and downstream.score1 is None
        and downstream.score2 is None
        for downstream in _result_downstream_matches(tournament, match)
    )


def _maybe_complete(tournament, source_match=None):
    """Finish the tournament once the final — and the 3rd-place match, when
    the bracket has one — are both decided."""
    final, third = _final_and_third(tournament)
    if not final or final.winner_entry_id is None:
        return
    if third and third.winner_entry_id is None:
        return
    _complete_tournament(
        tournament, final.winner_entry_id, source_match=source_match,
    )


def _notify_matchup(tournament, match, label):
    entries = {e.id: e for e in tournament.entries}
    e1, e2 = entries.get(match.entry1_id), entries.get(match.entry2_id)
    if e1 and e2:
        _notify_entry(
            e1, 'tournament_match',
            f'{tournament.name}: your {label} vs {e2.display_name()} is set',
            action_url=_match_action_url(tournament, match),
        )
        _notify_entry(
            e2, 'tournament_match',
            f'{tournament.name}: your {label} vs {e1.display_name()} is set',
            action_url=_match_action_url(tournament, match),
        )


def _advance_winner(tournament, match, total_rounds, notify_ready=True):
    """Push a decided match's winner into the next round's slot (and a
    semifinal loser into the 3rd-place match); crown the champion when the
    last round is decided."""
    if match.round >= total_rounds:
        _maybe_complete(tournament, source_match=match)
        return
    nxt = next(
        (m for m in tournament.matches
         if m.round == match.round + 1 and m.position == match.position // 2),
        None,
    )
    if not nxt:
        return
    winner = next(
        (entry for entry in tournament.entries if entry.id == match.winner_entry_id),
        None,
    )
    if match.position % 2 == 0:
        nxt.entry1_id = match.winner_entry_id
        nxt.entry1 = winner
    else:
        nxt.entry2_id = match.winner_entry_id
        nxt.entry2 = winner
    if notify_ready and nxt.entry1_id and nxt.entry2_id:
        _notify_matchup(tournament, nxt, _round_label(nxt.round, tournament.total_rounds()))

    # Semifinal losers drop into the 3rd-place match.
    if match.round == total_rounds - 1:
        _, third = _final_and_third(tournament)
        if third:
            loser_id = match.entry2_id if match.winner_entry_id == match.entry1_id \
                else match.entry1_id
            loser = next(
                (entry for entry in tournament.entries if entry.id == loser_id),
                None,
            )
            if match.position % 2 == 0:
                third.entry1_id = loser_id
                third.entry1 = loser
            else:
                third.entry2_id = loser_id
                third.entry2 = loser
            if notify_ready and third.entry1_id and third.entry2_id:
                _notify_matchup(tournament, third, '3rd-place match')


def _round_label(round_num, total_rounds):
    remaining = total_rounds - round_num
    if remaining == 0:
        return 'final'
    if remaining == 1:
        return 'semifinal'
    if remaining == 2:
        return 'quarterfinal'
    return f'round {round_num} match'


def _complete_tournament(tournament, champion_entry_id, source_match=None):
    if tournament.status == 'completed':
        return False
    champion = next(
        (e for e in tournament.entries if e.id == champion_entry_id), None,
    )
    tournament.status = 'completed'
    # Assign the relationship, not the FK, so the champion serializes in this
    # same request without a stale lazy-load.
    tournament.champion_entry = champion
    tournament.completed_at = utcnow()
    action_url = _match_action_url(tournament, source_match) if source_match else ''

    # Ranked tournaments settle ELO once, here, when results are final —
    # corrections during play never double-apply.
    if tournament.ranked:
        from backend.routes.games import _apply_elo
        entries = {e.id: e for e in tournament.entries}
        totals = {}
        for match in sorted(tournament.matches, key=lambda m: (m.round, m.position)):
            if match.winner_entry_id is None or match.score1 is None:
                continue  # byes and (theoretical) undecided matches don't rate
            e1, e2 = entries.get(match.entry1_id), entries.get(match.entry2_id)
            if not e1 or not e2:
                continue
            deltas = _apply_elo(
                e1.players(), e2.players(),
                team1_won=match.winner_entry_id == match.entry1_id,
            )
            for uid, delta in deltas.items():
                totals[uid] = totals.get(uid, 0) + delta
        for uid, total in totals.items():
            notify(
                uid,
                'ranked_result',
                f'{tournament.name} rating: {"+" if total >= 0 else ""}{total}',
                related_tournament_id=tournament.id,
                action_url=action_url,
            )

    champ_name = champion.display_name() if champion else 'The winner'
    for uid in tournament.participant_ids() | {tournament.organizer_id}:
        notify(
            uid,
            'tournament_result',
            f'{champ_name} won {tournament.name}',
            related_tournament_id=tournament.id,
            action_url=action_url,
        )
    return True


@tournaments_bp.post('/tournaments/<int:tournament_id>/start')
@rate_limit(20, 3600)
@login_required
def start_tournament(tournament_id):
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'tournament_not_found'}), 404
    if tournament.organizer_id != g.current_user.id:
        return jsonify({'error': 'not_organizer'}), 403
    if tournament.status != 'registration':
        return jsonify({'error': 'already_started'}), 409
    entries = list(tournament.entries)
    if len(entries) < MIN_ENTRIES:
        return jsonify({'error': 'not_enough_entries'}), 400

    # Seed by rating (doubles: pair average), best first.
    entries.sort(key=lambda e: -e.avg_rating())
    for i, entry in enumerate(entries):
        entry.seed = i + 1

    # Matches are created via the relationship so tournament.matches is live
    # for the bye propagation and serialization below.
    if tournament.format == 'round_robin':
        for round_num, pairs in enumerate(_round_robin_rounds([e.id for e in entries]), 1):
            for pos, (a, b) in enumerate(pairs):
                db.session.add(TournamentMatch(
                    tournament=tournament, round=round_num, position=pos,
                    entry1_id=a, entry2_id=b,
                ))
    else:
        size = 2
        while size < len(entries):
            size *= 2
        by_seed = {e.seed: e.id for e in entries}
        slots = [by_seed.get(s) for s in _seed_slot_order(size)]
        total_rounds = size.bit_length() - 1
        for round_num in range(1, total_rounds + 1):
            for pos in range(size // (2 ** round_num)):
                match = TournamentMatch(
                    tournament=tournament, round=round_num, position=pos,
                )
                if round_num == 1:
                    match.entry1_id = slots[pos * 2]
                    match.entry2_id = slots[pos * 2 + 1]
                db.session.add(match)
        # A bronze match needs two semifinal losers — guaranteed only when at
        # least 4 entries exist (3-entry brackets have a bye semifinal).
        if len(entries) >= 4:
            db.session.add(TournamentMatch(
                tournament=tournament, round=total_rounds, position=1,
            ))
        # Audit events require durable match ids; flush the whole bracket before
        # automatic byes are marked and propagated.
        db.session.flush()
        _propagate_bye_wins(tournament)

    tournament.status = 'active'
    for entry in entries:
        _notify_entry(
            entry, 'tournament_start',
            f'{tournament.name} has started — the bracket is out. '
            f'You are seed {entry.seed}.',
        )
    db.session.commit()
    return jsonify(_detail_payload(tournament, g.current_user.id))


def _score_summary(tournament, match):
    entry1, entry2 = _match_entries(tournament, match)
    left = entry1.display_name() if entry1 else 'Entry 1'
    right = entry2.display_name() if entry2 else 'Entry 2'
    return f'{left} {match.score1}–{match.score2} {right}'


def _notify_score_submission(tournament, match, actor_id):
    participants = _match_participant_ids(tournament, match)
    eligible = {
        user_id for user_id in participants
        if _eligible_result_confirmer(tournament, match, user_id)
    }
    summary = _score_summary(tournament, match)
    _notify_result_users(
        tournament,
        match,
        eligible,
        f'{tournament.name}: confirm the submitted score',
        summary,
        actor_id=actor_id,
    )
    observers = (participants | {tournament.organizer_id}) - eligible
    _notify_result_users(
        tournament,
        match,
        observers,
        f'{tournament.name}: score submitted',
        summary,
        actor_id=actor_id,
    )


def _notify_score_action(tournament, match, actor_id, action, reason=''):
    body = _score_summary(tournament, match)
    if reason:
        body = f'{body} · {reason}'
    _notify_result_users(
        tournament,
        match,
        _match_participant_ids(tournament, match) | {tournament.organizer_id},
        f'{tournament.name}: score {action}',
        body,
        actor_id=actor_id,
    )


def _progress_confirmed_result(tournament, match, notify_ready=True):
    winner_id = match.entry1_id if match.score1 > match.score2 else match.entry2_id
    winner = next(
        (entry for entry in tournament.entries if entry.id == winner_id),
        None,
    )
    match.winner_entry_id = winner_id
    match.winner_entry = winner
    if tournament.format == 'single_elim':
        _advance_winner(
            tournament, match, tournament.total_rounds(),
            notify_ready=notify_ready,
        )
        _propagate_bye_wins(tournament, notify_ready=notify_ready)
    elif all(candidate.winner_entry_id is not None for candidate in tournament.matches):
        _complete_tournament(
            tournament, _top_of_standings(tournament), source_match=match,
        )


def _result_request_context(tournament_id, match_id):
    tournament, match = _locked_tournament_match(tournament_id, match_id)
    if not tournament:
        return None, None, (jsonify({'error': 'tournament_not_found'}), 404)
    if not match:
        return tournament, None, (jsonify({'error': 'match_not_found'}), 404)
    if tournament.status != 'active':
        return tournament, match, (jsonify({'error': 'tournament_not_active'}), 409)
    if match.entry1_id is None or match.entry2_id is None:
        return tournament, match, (jsonify({'error': 'match_not_ready'}), 409)
    return tournament, match, None


def _check_result_version(payload, tournament, match):
    expected, error = _parse_expected_result_version(payload)
    if error:
        return jsonify({'error': error}), 400
    if expected is not None and expected != int(match.result_version or 0):
        return _stale_result_response(tournament, match)
    return None


@tournaments_bp.post('/tournaments/<int:tournament_id>/matches/<int:match_id>/score')
@rate_limit(120, 3600)
@login_required
def score_match(tournament_id, match_id):
    tournament, match, error = _result_request_context(tournament_id, match_id)
    if error:
        return error
    user_id = g.current_user.id
    if user_id != tournament.organizer_id \
            and user_id not in _match_participant_ids(tournament, match):
        return jsonify({'error': 'not_allowed'}), 403

    payload = _result_json_payload()
    version_error = _check_result_version(payload, tournament, match)
    if version_error:
        return version_error
    score = _parse_score_pair(payload)
    if not score:
        return jsonify({'error': 'invalid_score'}), 400
    state = match.effective_result_state()
    if state not in ('unreported', 'disputed'):
        return jsonify({'error': 'result_not_reportable'}), 409
    if state == 'unreported' and (
            match.score1 is not None or match.score2 is not None
            or match.reported_by_id is not None):
        return jsonify({'error': 'result_not_reportable'}), 409

    now = utcnow()
    match.score1, match.score2 = score
    match.winner_entry_id = None
    match.winner_entry = None
    match.result_state = 'awaiting_confirmation'
    match.reported_by_id = user_id
    match.reported_by = g.current_user
    match.reported_at = now
    match.confirmed_by_id = None
    match.confirmed_by = None
    match.confirmed_at = None
    match.disputed_by_id = None
    match.disputed_by = None
    match.disputed_at = None
    match.dispute_reason = ''
    match.resolution_kind = ''
    _record_result_action(match, 'reported', actor_id=user_id)
    _notify_score_submission(tournament, match, user_id)
    return _commit_result_change(tournament_id, match_id)


@tournaments_bp.post('/tournaments/<int:tournament_id>/matches/<int:match_id>/confirm')
@rate_limit(120, 3600)
@login_required
def confirm_match_result(tournament_id, match_id):
    tournament, match, error = _result_request_context(tournament_id, match_id)
    if error:
        return error
    user_id = g.current_user.id
    if not _eligible_result_confirmer(tournament, match, user_id):
        return jsonify({'error': 'not_allowed'}), 403

    payload = _result_json_payload()
    version_error = _check_result_version(payload, tournament, match)
    if version_error:
        return version_error
    if match.effective_result_state() != 'awaiting_confirmation':
        return jsonify({'error': 'result_not_awaiting_confirmation'}), 409
    if not _parse_score_pair({}, stored_match=match):
        return jsonify({'error': 'score_missing'}), 409

    now = utcnow()
    match.result_state = 'confirmed'
    match.confirmed_by_id = user_id
    match.confirmed_by = g.current_user
    match.confirmed_at = now
    match.resolution_kind = 'participant_confirmation'
    _record_result_action(match, 'confirmed', actor_id=user_id)
    _progress_confirmed_result(tournament, match)
    _notify_score_action(tournament, match, user_id, 'confirmed')
    return _commit_result_change(tournament_id, match_id)


@tournaments_bp.post('/tournaments/<int:tournament_id>/matches/<int:match_id>/dispute')
@rate_limit(120, 3600)
@login_required
def dispute_match_result(tournament_id, match_id):
    tournament, match, error = _result_request_context(tournament_id, match_id)
    if error:
        return error
    user_id = g.current_user.id
    if not _eligible_result_confirmer(tournament, match, user_id):
        return jsonify({'error': 'not_allowed'}), 403

    payload = _result_json_payload()
    version_error = _check_result_version(payload, tournament, match)
    if version_error:
        return version_error
    if match.effective_result_state() != 'awaiting_confirmation':
        return jsonify({'error': 'result_not_awaiting_confirmation'}), 409
    reason = str(payload.get('reason') or payload.get('dispute_reason') or '').strip()[:500]
    if not reason:
        return jsonify({'error': 'reason_required'}), 400

    match.result_state = 'disputed'
    match.winner_entry_id = None
    match.winner_entry = None
    match.disputed_by_id = user_id
    match.disputed_by = g.current_user
    match.disputed_at = utcnow()
    match.dispute_reason = reason
    match.resolution_kind = ''
    _record_result_action(
        match, 'disputed', actor_id=user_id, reason=reason,
    )
    _notify_score_action(tournament, match, user_id, 'disputed', reason)
    return _commit_result_change(tournament_id, match_id)


@tournaments_bp.post('/tournaments/<int:tournament_id>/matches/<int:match_id>/resolve')
@rate_limit(120, 3600)
@login_required
def resolve_match_result(tournament_id, match_id):
    tournament, match, error = _result_request_context(tournament_id, match_id)
    if error:
        return error
    if tournament.organizer_id != g.current_user.id:
        return jsonify({'error': 'not_organizer'}), 403

    payload = _result_json_payload()
    version_error = _check_result_version(payload, tournament, match)
    if version_error:
        return version_error
    reason = str(payload.get('reason') or payload.get('resolution_reason') or '').strip()[:500]
    if not reason:
        return jsonify({'error': 'reason_required'}), 400
    state = match.effective_result_state()
    if state not in ('awaiting_confirmation', 'disputed', 'confirmed'):
        return jsonify({'error': 'result_not_resolvable'}), 409
    correction = state == 'confirmed'
    if correction and not _confirmed_correction_is_safe(tournament, match):
        return jsonify({'error': 'next_match_played'}), 409
    downstream_before = {
        downstream.id: (downstream.entry1_id, downstream.entry2_id)
        for downstream in _result_downstream_matches(tournament, match)
    } if correction else {}
    score = _parse_score_pair(payload, stored_match=match)
    if not score:
        status = 400 if 'score1' in payload or 'score2' in payload else 409
        return jsonify({'error': 'invalid_score' if status == 400 else 'score_missing'}), status

    user_id = g.current_user.id
    match.score1, match.score2 = score
    match.result_state = 'confirmed'
    match.confirmed_by_id = user_id
    match.confirmed_by = g.current_user
    match.confirmed_at = utcnow()
    match.resolution_kind = 'organizer_correction' if correction else 'organizer_resolution'
    _record_result_action(
        match,
        'corrected' if correction else 'resolved',
        actor_id=user_id,
        reason=reason,
    )
    _progress_confirmed_result(
        tournament, match, notify_ready=not correction,
    )
    if correction:
        for downstream in _result_downstream_matches(tournament, match):
            before = downstream_before.get(downstream.id)
            after = (downstream.entry1_id, downstream.entry2_id)
            if before == after or not all(after):
                continue
            label = '3rd-place match' if (
                downstream.round == tournament.total_rounds()
                and downstream.position == 1
            ) else _round_label(downstream.round, tournament.total_rounds())
            _notify_matchup(tournament, downstream, label)
    _notify_score_action(
        tournament,
        match,
        user_id,
        'corrected' if correction else 'resolved',
        reason,
    )
    return _commit_result_change(tournament_id, match_id)


def _top_of_standings(tournament):
    table = _standings(tournament)
    return table[0]['entry']['id'] if table else None


REMINDER_LEAD_MINUTES = 65


def send_tournament_reminders():
    """Lazy sweep (runs on /me reads, like game reminders): hour-before and
    day-before nudges to every participant, at most once each per tournament."""
    now = utcnow()
    open_statuses = ('registration', 'active')

    hour_due = Tournament.query.filter(
        Tournament.status.in_(open_statuses),
        Tournament.reminded_at.is_(None),
        Tournament.starts_at > now,
        Tournament.starts_at <= now + timedelta(minutes=REMINDER_LEAD_MINUTES),
    ).all()
    day_due = Tournament.query.filter(
        Tournament.status.in_(open_statuses),
        Tournament.day_reminded_at.is_(None),
        Tournament.starts_at > now + timedelta(hours=20),
        Tournament.starts_at <= now + timedelta(hours=28),
    ).all()

    changed = False
    for tournament, title in (
        [(t, f'{t.name} starts in about an hour — check in when you arrive')
         for t in hour_due]
        + [(t, f'{t.name} is tomorrow — get your paddle ready')
           for t in day_due]
    ):
        for uid in tournament.participant_ids():
            notify(
                uid,
                'tournament_reminder',
                title,
                related_tournament_id=tournament.id,
            )
        if tournament in hour_due:
            tournament.reminded_at = now
        else:
            tournament.day_reminded_at = now
        changed = True
    if changed:
        db.session.commit()


CHECKIN_OPENS_HOURS_BEFORE = 24


@tournaments_bp.post('/tournaments/<int:tournament_id>/checkin')
@rate_limit(60, 3600)
@login_required
def tournament_checkin(tournament_id):
    """Day-of arrival confirmation — either partner can check the entry in,
    from 24h before the start until the tournament wraps."""
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'tournament_not_found'}), 404
    if tournament.status not in ('registration', 'active'):
        return jsonify({'error': 'tournament_not_open'}), 409
    entry = tournament.entry_for(g.current_user.id)
    if not entry:
        return jsonify({'error': 'not_registered'}), 404
    if utcnow() < tournament.starts_at - timedelta(hours=CHECKIN_OPENS_HOURS_BEFORE):
        return jsonify({'error': 'checkin_not_open'}), 409
    if entry.checked_in_at is None:
        entry.checked_in_at = utcnow()
        db.session.commit()
    return jsonify(_detail_payload(tournament, g.current_user.id))


@tournaments_bp.post('/tournaments/<int:tournament_id>/cancel')
@rate_limit(20, 3600)
@login_required
def cancel_tournament(tournament_id):
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'tournament_not_found'}), 404
    if tournament.organizer_id != g.current_user.id:
        return jsonify({'error': 'not_organizer'}), 403
    if tournament.status in ('completed', 'cancelled'):
        return jsonify({'error': 'already_finished'}), 409
    tournament.status = 'cancelled'
    for uid in tournament.participant_ids() - {g.current_user.id}:
        notify(
            uid,
            'tournament_cancelled',
            f'{tournament.name} was cancelled by the organizer',
            related_user_id=g.current_user.id,
            related_tournament_id=tournament.id,
        )
    db.session.commit()
    return jsonify(tournament.to_dict(g.current_user.id))
