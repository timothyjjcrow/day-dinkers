"""Tournaments: registration, seeded brackets, score reporting, standings."""
from datetime import timedelta

from flask import Blueprint, g, jsonify, request

from backend.app import db
from backend.models import (
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


def _notify_entry(entry, kind, title, body='', related_user_id=None):
    for player in entry.players():
        notify(
            player.id, kind, title, body,
            related_user_id=related_user_id,
            related_tournament_id=entry.tournament_id,
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
    if tournament.format == 'round_robin':
        data['standings'] = _standings(tournament)
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

    tournament = Tournament(
        name=name,
        description=str(payload.get('description') or '').strip()[:500],
        court_id=court.id,
        organizer_id=g.current_user.id,
        starts_at=starts_at,
        format=fmt,
        event_type=event_type,
        max_entries=max_entries,
    )
    db.session.add(tournament)
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
            match.winner_entry_id = match.entry1_id if has1 else match.entry2_id
            _advance_winner(tournament, match, total, notify_ready=notify_ready)
            changed = True


def _feeders_settled(tournament, match):
    """True when every feeder match of this slot has produced its winner (or
    the slot never had a feeder, i.e. it's a round-1 bye)."""
    if match.round == 1:
        return True
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


def _maybe_complete(tournament):
    """Finish the tournament once the final — and the 3rd-place match, when
    the bracket has one — are both decided."""
    final, third = _final_and_third(tournament)
    if not final or final.winner_entry_id is None:
        return
    if third and third.winner_entry_id is None:
        return
    _complete_tournament(tournament, final.winner_entry_id)


def _notify_matchup(tournament, match, label):
    entries = {e.id: e for e in tournament.entries}
    e1, e2 = entries.get(match.entry1_id), entries.get(match.entry2_id)
    if e1 and e2:
        _notify_entry(
            e1, 'tournament_match',
            f'{tournament.name}: your {label} vs {e2.display_name()} is set',
        )
        _notify_entry(
            e2, 'tournament_match',
            f'{tournament.name}: your {label} vs {e1.display_name()} is set',
        )


def _advance_winner(tournament, match, total_rounds, notify_ready=True):
    """Push a decided match's winner into the next round's slot (and a
    semifinal loser into the 3rd-place match); crown the champion when the
    last round is decided."""
    if match.round >= total_rounds:
        _maybe_complete(tournament)
        return
    nxt = next(
        (m for m in tournament.matches
         if m.round == match.round + 1 and m.position == match.position // 2),
        None,
    )
    if not nxt:
        return
    if match.position % 2 == 0:
        nxt.entry1_id = match.winner_entry_id
    else:
        nxt.entry2_id = match.winner_entry_id
    if notify_ready and nxt.entry1_id and nxt.entry2_id:
        _notify_matchup(tournament, nxt, _round_label(nxt.round, tournament.total_rounds()))

    # Semifinal losers drop into the 3rd-place match.
    if match.round == total_rounds - 1:
        _, third = _final_and_third(tournament)
        if third:
            loser_id = match.entry2_id if match.winner_entry_id == match.entry1_id \
                else match.entry1_id
            if match.position % 2 == 0:
                third.entry1_id = loser_id
            else:
                third.entry2_id = loser_id
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


def _complete_tournament(tournament, champion_entry_id):
    champion = next(
        (e for e in tournament.entries if e.id == champion_entry_id), None,
    )
    tournament.status = 'completed'
    # Assign the relationship, not the FK, so the champion serializes in this
    # same request without a stale lazy-load.
    tournament.champion_entry = champion
    tournament.completed_at = utcnow()
    champ_name = champion.display_name() if champion else 'The winner'
    for uid in tournament.participant_ids() | {tournament.organizer_id}:
        notify(
            uid,
            'tournament_result',
            f'{champ_name} won {tournament.name}',
            related_tournament_id=tournament.id,
        )


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


@tournaments_bp.post('/tournaments/<int:tournament_id>/matches/<int:match_id>/score')
@rate_limit(120, 3600)
@login_required
def score_match(tournament_id, match_id):
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'tournament_not_found'}), 404
    if tournament.status != 'active':
        return jsonify({'error': 'tournament_not_active'}), 409
    match = next((m for m in tournament.matches if m.id == match_id), None)
    if not match:
        return jsonify({'error': 'match_not_found'}), 404
    if match.entry1_id is None or match.entry2_id is None:
        return jsonify({'error': 'match_not_ready'}), 409

    entries = {e.id: e for e in tournament.entries}
    e1, e2 = entries.get(match.entry1_id), entries.get(match.entry2_id)
    is_participant = any(
        g.current_user.id in (p.id for p in e.players())
        for e in (e1, e2) if e
    )
    if not is_participant and tournament.organizer_id != g.current_user.id:
        return jsonify({'error': 'not_allowed'}), 403

    payload = request.get_json(silent=True) or {}
    try:
        score1, score2 = int(payload.get('score1')), int(payload.get('score2'))
    except (TypeError, ValueError):
        return jsonify({'error': 'invalid_score'}), 400
    if not (0 <= score1 <= 99 and 0 <= score2 <= 99) or score1 == score2:
        return jsonify({'error': 'invalid_score'}), 400

    total = tournament.total_rounds()
    previous_winner = match.winner_entry_id
    new_winner = match.entry1_id if score1 > score2 else match.entry2_id
    is_semifinal = tournament.format == 'single_elim' and match.round == total - 1

    if previous_winner is not None and tournament.format == 'single_elim':
        # A correction is only safe while the matches it feeds haven't been
        # played — for a semifinal that's both the final and the 3rd-place match.
        nxt = next(
            (m for m in tournament.matches
             if m.round == match.round + 1 and m.position == match.position // 2),
            None,
        )
        if nxt and nxt.winner_entry_id is not None:
            return jsonify({'error': 'next_match_played'}), 409
        if is_semifinal:
            _, third = _final_and_third(tournament)
            if third and third.winner_entry_id is not None:
                return jsonify({'error': 'next_match_played'}), 409

    match.score1, match.score2 = score1, score2
    match.winner_entry_id = new_winner
    if tournament.format == 'single_elim':
        if previous_winner is None or previous_winner != new_winner:
            _advance_winner(tournament, match, total)
    elif all(m.winner_entry_id is not None for m in tournament.matches):
        _complete_tournament(tournament, _top_of_standings(tournament))

    # Tell the losing side (and organizer) the result landed.
    loser = e2 if new_winner == match.entry1_id else e1
    winner = e1 if new_winner == match.entry1_id else e2
    if loser and winner and tournament.status == 'active':
        for player in loser.players() + winner.players():
            if player.id != g.current_user.id:
                notify(
                    player.id,
                    'tournament_score',
                    f'{tournament.name}: {winner.display_name()} beat '
                    f'{loser.display_name()} {max(score1, score2)}–{min(score1, score2)}',
                    related_user_id=g.current_user.id,
                    related_tournament_id=tournament.id,
                )
    db.session.commit()
    return jsonify(_detail_payload(tournament, g.current_user.id))


def _top_of_standings(tournament):
    table = _standings(tournament)
    return table[0]['entry']['id'] if table else None


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
