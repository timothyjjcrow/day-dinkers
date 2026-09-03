"""Tournaments: registration, seeded brackets, score reporting, standings."""
from datetime import timedelta
import json
import math

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy.exc import IntegrityError

from backend.app import db
from backend.models import (
    CompetitionResultEvent,
    Court,
    Notification,
    TOURNAMENT_EVENT_TYPES,
    TOURNAMENT_FORMATS,
    TOURNAMENT_GAME_FORMATS,
    Tournament,
    TournamentEntry,
    TournamentMatch,
    User,
    award_new_badges,
    iso,
    is_blocked_between,
    notify,
    utcnow,
)
from backend.routes.auth import login_required
from backend.routes.competition_http import conditional_competition_detail
from backend.routes.courts import haversine_miles
from backend.routes.games import (
    _encode_page_cursor,
    _page_args,
    _page_payload,
    _parse_scheduled_at,
)
from backend.security import rate_limit

tournaments_bp = Blueprint('tournaments', __name__)

MIN_ENTRIES = 2
MAX_ENTRIES_CAP = 32
MAX_TOURNAMENT_COURTS = 24
GAME_FORMAT_RULES = {
    'single_11': {'label': 'One game to 11', 'target': 11, 'wins': 1, 'max_games': 1},
    'single_15': {'label': 'One game to 15', 'target': 15, 'wins': 1, 'max_games': 1},
    'best_of_3_11': {'label': 'Best of 3 to 11', 'target': 11, 'wins': 2, 'max_games': 3},
}


def _tournament_result_window_hours():
    return max(1, int(current_app.config.get(
        'TOURNAMENT_RESULT_AUTO_CONFIRM_HOURS', 2,
    )))


def _result_nudge_cooldown():
    return timedelta(minutes=max(1, int(current_app.config.get(
        'COMPETITION_RESULT_NUDGE_COOLDOWN_MINUTES', 30,
    ))))


def _result_review_deadline(reported_at):
    if not reported_at:
        return None
    return reported_at + timedelta(hours=_tournament_result_window_hours())


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


def _schedule_tournament_matches(tournament):
    """Give every generated slot an honest estimated time and court number."""
    courts = max(1, int(tournament.court_count or 1))
    duration = max(15, int(tournament.match_minutes or 30))
    round_start = tournament.starts_at
    for round_number in sorted({match.round for match in tournament.matches}):
        matches = sorted(
            (match for match in tournament.matches if match.round == round_number),
            key=lambda match: (match.position, match.id or 0),
        )
        for index, match in enumerate(matches):
            match.court_number = index % courts + 1
            match.scheduled_at = round_start + timedelta(
                minutes=(index // courts) * duration,
            )
        waves = max(1, math.ceil(len(matches) / courts))
        round_start += timedelta(minutes=waves * duration)


def _tournament_schedule_estimate(tournament):
    """Return an honest event duration/end for calendar and share surfaces.

    Once a bracket exists its latest scheduled match is authoritative. During
    registration, estimate the complete configured field so an exported event
    cannot quietly promise an arbitrary four-hour block.
    """
    courts = max(1, int(tournament.court_count or 1))
    match_minutes = max(15, int(tournament.match_minutes or 30))
    scheduled = [match.scheduled_at for match in tournament.matches if match.scheduled_at]
    if scheduled and tournament.starts_at:
        end_at = max(scheduled) + timedelta(minutes=match_minutes)
        duration = max(
            match_minutes,
            math.ceil((end_at - tournament.starts_at).total_seconds() / 60),
        )
        return duration, end_at

    entries = max(MIN_ENTRIES, int(tournament.max_entries or MIN_ENTRIES))
    if tournament.format == 'round_robin':
        rounds = entries - 1 if entries % 2 == 0 else entries
        matches_per_round = entries // 2
        waves = max(1, math.ceil(matches_per_round / courts))
        duration = rounds * waves * match_minutes
    else:
        bracket_size = 2
        while bracket_size < entries:
            bracket_size *= 2
        round_matches = [
            bracket_size // (2 ** round_number)
            for round_number in range(1, bracket_size.bit_length())
        ]
        if entries >= 4:
            round_matches[-1] += 1  # final and bronze match share the last round
        duration = sum(
            max(1, math.ceil(match_count / courts)) * match_minutes
            for match_count in round_matches
        )
    end_at = tournament.starts_at + timedelta(minutes=duration) \
        if tournament.starts_at else None
    return duration, end_at


def _add_tournament_schedule_estimate(data, tournament):
    duration, end_at = _tournament_schedule_estimate(tournament)
    data['estimated_duration_minutes'] = duration
    data['estimated_end_at'] = iso(end_at)
    return data


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


def _result_confirmer_ids(tournament, match):
    return {
        user_id
        for user_id in _match_participant_ids(tournament, match)
        if _eligible_result_confirmer(tournament, match, user_id)
    }


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


def _parse_game_scores(payload, tournament, stored_match=None):
    """Validate the configured match format while retaining legacy clients.

    Older clients send one ``score1``/``score2`` pair. New clients send the
    individual ``games`` ledger; score1/score2 then remain the compatible
    aggregate used by bracket progression and historical consumers.
    """
    if 'games' not in payload:
        configured_format = getattr(tournament, 'game_format', None) or 'single_11'
        existing = stored_match.game_scores() if stored_match else []
        # Only the original one-game-to-11 contract accepts the legacy pair.
        # A configured alternate format must send its per-game ledger so the
        # server can enforce the target and match winner.
        if configured_format != 'single_11' and not existing:
            return None
        pair = _parse_score_pair(payload, stored_match=stored_match)
        if not pair:
            return None
        games = existing if existing and not {'score1', 'score2'} & payload.keys() else [
            {'score1': pair[0], 'score2': pair[1]},
        ]
        return pair, games

    rows = payload.get('games')
    rule = GAME_FORMAT_RULES.get(
        getattr(tournament, 'game_format', None) or 'single_11'
    )
    if not rule or not isinstance(rows, list) or not rows:
        return None
    if len(rows) > rule['max_games']:
        return None

    games = []
    wins = [0, 0]
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            return None
        raw1, raw2 = row.get('score1'), row.get('score2')
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
        high, low = max(score1, score2), min(score1, score2)
        margin = high - low
        valid_finish = (
            high == rule['target'] and margin >= 2
        ) or (
            high > rule['target'] and margin == 2
        )
        if low < 0 or high > 99 or not valid_finish:
            return None
        winner = 0 if score1 > score2 else 1
        wins[winner] += 1
        games.append({'score1': score1, 'score2': score2})
        if wins[winner] == rule['wins'] and index != len(rows) - 1:
            return None

    if max(wins) != rule['wins']:
        return None
    if rule['wins'] == 1 and len(games) != 1:
        return None
    return (
        (games[0]['score1'], games[0]['score2'])
        if rule['wins'] == 1 else (wins[0], wins[1]),
        games,
    )


def _parse_optional_division_rating(value):
    if value in (None, ''):
        return None
    if isinstance(value, bool):
        raise ValueError
    parsed = float(value)
    if not 2.0 <= parsed <= 5.5:
        raise ValueError
    return round(parsed, 1)


def _division_settings(payload, fallback=None):
    fallback = fallback or {}
    try:
        minimum = _parse_optional_division_rating(
            payload.get('division_min_rating', fallback.get('division_min_rating'))
        )
        maximum = _parse_optional_division_rating(
            payload.get('division_max_rating', fallback.get('division_max_rating'))
        )
    except (TypeError, ValueError):
        return None, 'invalid_division'
    if (minimum is None) != (maximum is None) or (
        minimum is not None and minimum > maximum
    ):
        return None, 'invalid_division'
    name = str(payload.get('division_name', fallback.get('division_name') or '') or '').strip()[:80]
    if not name:
        name = 'Open' if minimum is None else f'{minimum:.1f}–{maximum:.1f}'
    return {
        'division_name': name,
        'division_min_rating': minimum,
        'division_max_rating': maximum,
    }, None


def _division_registration_error(tournament, user):
    minimum = tournament.division_min_rating
    maximum = tournament.division_max_rating
    if minimum is None and maximum is None:
        return None
    rating = user.skill_rating
    if rating is None:
        return 'skill_rating_required'
    if (
        minimum is not None and rating < minimum
    ) or (
        maximum is not None and rating > maximum
    ):
        return 'outside_division'
    return None


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


def _notify_result_users(tournament, match, user_ids, title, body='', actor_id=None,
                         unread_dedupe_key=''):
    for user_id in set(user_ids) - ({actor_id} if actor_id else set()):
        notify(
            user_id,
            'tournament_score',
            title,
            body,
            related_user_id=actor_id,
            related_tournament_id=tournament.id,
            action_url=_match_action_url(tournament, match),
            unread_dedupe_key=unread_dedupe_key,
        )


def _standings(tournament):
    """Round-robin table: wins, then point diff, then points for."""
    rows = {
        e.id: {'entry': e.to_dict(), 'wins': 0, 'losses': 0, 'points_for': 0,
               'points_against': 0}
        for e in tournament.entries
    }
    for m in tournament.matches:
        if m.winner_entry_id is None:
            continue
        # A recorded forfeit counts as a win/loss but invents no points or
        # point differential. Byes have a missing side and do not count here.
        if m.entry1_id is None or m.entry2_id is None:
            continue
        game_scores = m.game_scores()
        points1 = sum(game['score1'] for game in game_scores) \
            if game_scores else m.score1
        points2 = sum(game['score2'] for game in game_scores) \
            if game_scores else m.score2
        for eid, mine, theirs in (
            (m.entry1_id, points1, points2),
            (m.entry2_id, points2, points1),
        ):
            row = rows.get(eid)
            if not row:
                continue
            if mine is not None and theirs is not None:
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


def _tournament_action_summary(tournament, user_id):
    unresolved = [
        match for match in tournament.matches
        if match.effective_result_state() in (
            'awaiting_confirmation', 'disputed',
        )
    ]
    mine = [
        match for match in unresolved
        if match.effective_result_state() == 'awaiting_confirmation'
        and _eligible_result_confirmer(tournament, match, user_id)
    ]
    organizer_matches = unresolved if user_id == tournament.organizer_id else []
    unplayed = [
        match for match in tournament.matches
        if tournament.status == 'active'
        and match.entry1_id is not None and match.entry2_id is not None
        and match.effective_result_state() == 'unreported'
        and user_id in _match_participant_ids(tournament, match)
    ]
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
    partner_action = tournament.partner_action_for(user_id) \
        if tournament.status == 'registration' else None
    start_action_pending = bool(
        tournament.status == 'registration'
        and tournament.organizer_id == user_id
        and tournament.starts_at <= utcnow()
        and sum(
            entry.partner_ready(tournament.event_type)
            for entry in tournament.entries
        ) >= MIN_ENTRIES
    )
    return {
        'my_confirmation_count': len(mine),
        'unresolved_result_count': len(unresolved),
        'oldest_waiting_at': iso(
            ordered_unresolved[0].reported_at or ordered_unresolved[0].created_at
        ) if ordered_unresolved else None,
        'pending_action_count': (
            len(action_matches) + (1 if partner_action else 0)
            + int(start_action_pending)
        ),
        'action_match_id': ordered_actions[0].id if ordered_actions else None,
        'partner_action_pending': bool(partner_action),
        'start_action_pending': start_action_pending,
    }


def _summary_payload(tournament, user_id):
    data = tournament.to_dict(user_id)
    data.update(_tournament_action_summary(tournament, user_id))
    data['result_auto_confirm_hours'] = _tournament_result_window_hours()
    return _add_tournament_schedule_estimate(data, tournament)


def _detail_payload(tournament, user_id):
    data = tournament.to_dict(user_id, detail=True)
    data.update(_tournament_action_summary(tournament, user_id))
    data['result_auto_confirm_hours'] = _tournament_result_window_hours()
    _add_tournament_schedule_estimate(data, tournament)
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
        item['can_forfeit_result'] = bool(
            active and organizer
            and match.entry1_id is not None and match.entry2_id is not None
            and match.effective_result_state() in (
                'unreported', 'awaiting_confirmation', 'disputed',
            )
        )
        item['review_deadline_at'] = iso(
            _result_review_deadline(match.reported_at)
        )
        item['can_nudge_result'] = bool(
            active and organizer
            and match.effective_result_state() == 'awaiting_confirmation'
            and _result_confirmer_ids(tournament, match)
        )
        item['nudge_available_at'] = iso(
            match.last_nudged_at + _result_nudge_cooldown()
        ) if match.last_nudged_at else None
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
    limit, page_offset, page_error = _page_args(default=30, maximum=100)
    if page_error:
        return jsonify({'error': page_error}), 400
    if request.args.get('mine'):
        entered = (
            db.session.query(TournamentEntry.tournament_id)
            .filter(db.or_(
                TournamentEntry.player1_id == user_id,
                TournamentEntry.player2_id == user_id,
                TournamentEntry.partner_invitee_id == user_id,
            ))
        )
        query = (
            Tournament.query
            .filter(db.or_(
                Tournament.organizer_id == user_id,
                Tournament.id.in_(entered),
            ))
            .filter(Tournament.status != 'cancelled')
            .order_by(Tournament.starts_at.desc(), Tournament.id.desc())
        )
        total = query.count()
        rows = query.offset(page_offset).limit(limit).all()
        return jsonify(_page_payload(
            [_summary_payload(t, user_id) for t in rows],
            limit=limit,
            offset=page_offset,
            total=total,
            already_sliced=True,
        ))

    try:
        lat, lng = float(request.args.get('lat')), float(request.args.get('lng'))
        radius = min(max(float(request.args.get('radius', 60)), 1), 250)
    except (TypeError, ValueError):
        return jsonify({'error': 'lat_lng_required'}), 400
    lat_delta = radius / 69.0
    lng_delta = radius / max(0.1, 69.0 * math.cos(math.radians(lat)))
    query = (
        Tournament.query.join(Court)
        .filter(
            Tournament.status.in_(['registration', 'active']),
            Tournament.starts_at >= utcnow() - timedelta(days=3),
            Court.latitude.between(lat - lat_delta, lat + lat_delta),
            Court.longitude.between(lng - lng_delta, lng + lng_delta),
        )
        .order_by(Tournament.starts_at.asc(), Tournament.id.asc())
    )
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
        for tournament in rows:
            court = tournament.court
            if not court or court.latitude is None or court.longitude is None:
                continue
            if haversine_miles(
                lat, lng, court.latitude, court.longitude,
            ) > radius:
                continue
            if visible_before_page < page_offset:
                visible_before_page += 1
                continue
            if len(items) >= limit:
                has_more = True
                break
            items.append(_summary_payload(tournament, user_id))
        if has_more or len(rows) < batch_size:
            break
    return jsonify({
        'items': items,
        'count': len(items),
        'total': None if has_more else page_offset + len(items),
        'has_more': has_more,
        'next_cursor': _encode_page_cursor(page_offset + len(items))
        if has_more else None,
    })


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
    game_format = str(payload.get('game_format') or 'single_11').strip().lower()
    if game_format not in TOURNAMENT_GAME_FORMATS:
        return jsonify({'error': 'invalid_game_format'}), 400
    division, division_error = _division_settings(payload)
    if division_error:
        return jsonify({'error': division_error}), 400

    try:
        court_count = int(payload.get('court_count') or 1)
        match_minutes = int(payload.get('match_minutes') or 30)
    except (TypeError, ValueError):
        return jsonify({'error': 'invalid_tournament_schedule'}), 400
    if not 1 <= court_count <= MAX_TOURNAMENT_COURTS:
        return jsonify({'error': 'invalid_court_count'}), 400
    if not 15 <= match_minutes <= 120:
        return jsonify({'error': 'invalid_match_minutes'}), 400

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
        game_format=game_format,
        court_count=court_count,
        match_minutes=match_minutes,
        max_entries=max_entries,
        ranked=bool(payload.get('ranked')),
        **division,
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
    return jsonify(_detail_payload(tournament, g.current_user.id)), 201


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

    structural_fields = {
        'court_id', 'format', 'event_type', 'ranked', 'game_format',
        'division_name', 'division_min_rating', 'division_max_rating',
        'court_count', 'match_minutes',
    }
    if structural_fields & payload.keys():
        if tournament.status != 'registration':
            return jsonify({'error': 'registration_closed'}), 409
        if tournament.entries:
            return jsonify({'error': 'entries_lock_tournament_format'}), 409

        if 'court_id' in payload:
            try:
                court_id = int(payload.get('court_id') or 0)
            except (TypeError, ValueError):
                court_id = 0
            court = db.session.get(Court, court_id)
            if not court:
                return jsonify({'error': 'court_not_found'}), 404
            tournament.court = court

        if 'format' in payload:
            fmt = str(payload.get('format') or '').strip().lower()
            if fmt not in TOURNAMENT_FORMATS:
                return jsonify({'error': 'invalid_format'}), 400
            tournament.format = fmt
        if 'event_type' in payload:
            event_type = str(payload.get('event_type') or '').strip().lower()
            if event_type not in TOURNAMENT_EVENT_TYPES:
                return jsonify({'error': 'invalid_event_type'}), 400
            tournament.event_type = event_type
        if 'game_format' in payload:
            game_format = str(payload.get('game_format') or '').strip().lower()
            if game_format not in TOURNAMENT_GAME_FORMATS:
                return jsonify({'error': 'invalid_game_format'}), 400
            tournament.game_format = game_format

        division, division_error = _division_settings(payload, {
            'division_name': tournament.division_name,
            'division_min_rating': tournament.division_min_rating,
            'division_max_rating': tournament.division_max_rating,
        })
        if division_error:
            return jsonify({'error': division_error}), 400
        for field, value in division.items():
            setattr(tournament, field, value)

        if 'court_count' in payload:
            try:
                court_count = int(payload.get('court_count'))
            except (TypeError, ValueError):
                return jsonify({'error': 'invalid_court_count'}), 400
            if not 1 <= court_count <= MAX_TOURNAMENT_COURTS:
                return jsonify({'error': 'invalid_court_count'}), 400
            tournament.court_count = court_count
        if 'match_minutes' in payload:
            try:
                match_minutes = int(payload.get('match_minutes'))
            except (TypeError, ValueError):
                return jsonify({'error': 'invalid_match_minutes'}), 400
            if not 15 <= match_minutes <= 120:
                return jsonify({'error': 'invalid_match_minutes'}), 400
            tournament.match_minutes = match_minutes
        if 'ranked' in payload:
            tournament.ranked = bool(payload.get('ranked'))

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
    return conditional_competition_detail(
        _detail_payload(tournament, g.current_user.id),
        kind='tournament',
        entity_id=tournament.id,
        viewer_id=g.current_user.id,
    )


@tournaments_bp.patch('/tournaments/<int:tournament_id>/matches/<int:match_id>/schedule')
@rate_limit(60, 3600)
@login_required
def edit_tournament_match_schedule(tournament_id, match_id):
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'tournament_not_found'}), 404
    if tournament.organizer_id != g.current_user.id:
        return jsonify({'error': 'not_organizer'}), 403
    if tournament.status != 'active':
        return jsonify({'error': 'not_active'}), 409
    match = next((item for item in tournament.matches if item.id == match_id), None)
    if not match:
        return jsonify({'error': 'match_not_found'}), 404

    payload = request.get_json(silent=True) or {}
    if not {'scheduled_at', 'court_number'} & payload.keys():
        return jsonify({'error': 'schedule_required'}), 400
    changed = False
    if 'scheduled_at' in payload:
        scheduled_at = _parse_scheduled_at(payload.get('scheduled_at'))
        if not scheduled_at:
            return jsonify({'error': 'invalid_scheduled_at'}), 400
        changed = changed or scheduled_at != match.scheduled_at
        match.scheduled_at = scheduled_at
    if 'court_number' in payload:
        try:
            court_number = int(payload.get('court_number'))
        except (TypeError, ValueError):
            return jsonify({'error': 'invalid_match_court_number'}), 400
        if not 1 <= court_number <= max(1, int(tournament.court_count or 1)):
            return jsonify({'error': 'invalid_match_court_number'}), 400
        changed = changed or court_number != match.court_number
        match.court_number = court_number

    if changed:
        when = iso(match.scheduled_at) or 'time to be announced'
        for user_id in _match_participant_ids(tournament, match):
            if user_id == g.current_user.id:
                continue
            notify(
                user_id,
                'tournament_update',
                f'{tournament.name}: your match schedule changed',
                f'{when} · court {match.court_number or "to be announced"}',
                related_user_id=g.current_user.id,
                related_tournament_id=tournament.id,
                action_url=_match_action_url(tournament, match),
            )
    db.session.commit()
    return jsonify(_detail_payload(tournament, g.current_user.id))


def _partner_entry_for_invitee(tournament, user_id):
    return next((
        entry for entry in tournament.entries
        if entry.partner_status == 'pending'
        and entry.partner_invitee_id == user_id
    ), None)


def _partner_candidate_available(tournament, user_id, exclude_entry=None):
    """A player can occupy or await only one doubles slot per tournament."""
    return not any(
        entry is not exclude_entry and user_id in (
            entry.player1_id, entry.player2_id, entry.partner_invitee_id,
        )
        for entry in tournament.entries
    )


def _mark_partner_notifications_resolved(tournament, user_id, related_user_id=None):
    query = Notification.query.filter_by(
        user_id=user_id,
        kind='tournament_invite',
        related_tournament_id=tournament.id,
        read=False,
    )
    if related_user_id:
        query = query.filter(Notification.related_user_id == related_user_id)
    for notification in query.all():
        notification.read = True
        notification.unread_dedupe_key = None


def _notify_partner_invitation(tournament, entry, candidate, pending_on):
    owner = entry.player1
    if pending_on == 'owner':
        recipient = owner
        actor = candidate
        title = f'{candidate.display_name} offered to partner with you for {tournament.name}'
        body = 'Accept to complete your team, or decline and stay in the partner pool.'
    else:
        recipient = candidate
        actor = owner
        title = f'{owner.display_name} invited you to partner for {tournament.name}'
        body = 'Accept or decline before registration closes. You are not entered until you accept.'
    notify(
        recipient.id,
        'tournament_invite',
        title,
        body,
        related_user_id=actor.id,
        related_tournament_id=tournament.id,
        action_url=f'/#tournament/{tournament.id}',
    )


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
    division_error = _division_registration_error(tournament, user)
    if division_error:
        return jsonify({'error': division_error}), 409
    if tournament.entry_for(user.id):
        return jsonify({'error': 'already_registered'}), 409
    if _partner_entry_for_invitee(tournament, user.id):
        return jsonify({'error': 'partner_action_pending'}), 409

    partner = None
    needs_partner = False
    payload = request.get_json(silent=True) or {}
    if tournament.event_type == 'doubles':
        needs_partner = payload.get('needs_partner') is True
        try:
            partner_id = int(payload.get('partner_id') or 0)
        except (TypeError, ValueError):
            partner_id = 0
        partner = db.session.get(User, partner_id) if partner_id else None
        if not needs_partner:
            if not partner or partner.deleted_at or partner.id == user.id:
                return jsonify({'error': 'partner_choice_required'}), 400
            if is_blocked_between(user.id, partner.id):
                return jsonify({'error': 'blocked'}), 403
            partner_division_error = _division_registration_error(tournament, partner)
            if partner_division_error:
                return jsonify({
                    'error': 'partner_' + partner_division_error,
                }), 409
            if not _partner_candidate_available(tournament, partner.id):
                return jsonify({'error': 'partner_already_registered'}), 409

    # Assign the relationship (not the FK) so the tournament's loaded entries
    # collection includes this row when we serialize it below.
    entry = TournamentEntry(
        tournament=tournament,
        player1_id=user.id,
        player2_id=None,
        partner_invitee_id=partner.id if partner else None,
        partner_status=(
            'accepted' if tournament.event_type == 'singles'
            else 'pending' if partner else 'needed'
        ),
        partner_pending_on='invitee' if partner else '',
    )
    db.session.add(entry)
    db.session.flush()
    entry.player1 = user
    entry.partner_invitee = partner
    entry_name = user.display_name
    if partner:
        _notify_partner_invitation(tournament, entry, partner, 'invitee')
    if tournament.organizer_id != user.id:
        notify(
            tournament.organizer_id,
            'tournament_join',
            f'{entry_name} entered {tournament.name}' + (
                ' and is looking for a partner' if needs_partner
                else ' with a partner invitation pending' if partner
                else ''
            ),
            related_user_id=user.id,
            related_tournament_id=tournament.id,
            action_url=f'/#tournament/{tournament.id}',
        )
    db.session.commit()
    return jsonify(_detail_payload(tournament, user.id)), 201


@tournaments_bp.patch('/tournaments/<int:tournament_id>/register')
@rate_limit(30, 3600)
@login_required
def swap_partner(tournament_id):
    """The entry owner proposes a partner or returns to the partner pool."""
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
    needs_partner = payload.get('needs_partner') is True
    try:
        partner_id = int(payload.get('partner_id') or 0)
    except (TypeError, ValueError):
        partner_id = 0
    partner = db.session.get(User, partner_id) if partner_id else None
    if not needs_partner and (
        not partner or partner.deleted_at or partner.id == g.current_user.id
    ):
        return jsonify({'error': 'partner_choice_required'}), 400
    if partner and (
        partner.id == entry.player2_id
        or entry.partner_status == 'pending'
        and entry.partner_pending_on == 'invitee'
        and partner.id == entry.partner_invitee_id
    ):
        return jsonify(_detail_payload(tournament, g.current_user.id))
    if partner and is_blocked_between(g.current_user.id, partner.id):
        return jsonify({'error': 'blocked'}), 403
    if partner:
        partner_division_error = _division_registration_error(tournament, partner)
        if partner_division_error:
            return jsonify({
                'error': 'partner_' + partner_division_error,
            }), 409
    if partner and not _partner_candidate_available(
        tournament, partner.id, exclude_entry=entry,
    ):
        return jsonify({'error': 'partner_already_registered'}), 409

    old_partner_id = entry.player2_id
    old_invitee_id = entry.partner_invitee_id
    old_pending_on = entry.partner_pending_on
    if old_partner_id:
        notify(
            old_partner_id,
            'tournament_withdraw',
            f'{g.current_user.display_name} reopened their partner spot for {tournament.name}',
            related_user_id=g.current_user.id,
            related_tournament_id=tournament.id,
            action_url=f'/#tournament/{tournament.id}',
        )
    elif old_invitee_id:
        old_invitee = db.session.get(User, old_invitee_id)
        if old_invitee:
            notify(
                old_invitee.id,
                'tournament_withdraw',
                f'The pending partner request for {tournament.name} was cancelled',
                related_user_id=g.current_user.id,
                related_tournament_id=tournament.id,
                action_url=f'/#tournament/{tournament.id}',
            )
        decision_user_id = g.current_user.id \
            if old_pending_on == 'owner' else old_invitee_id
        _mark_partner_notifications_resolved(
            tournament, decision_user_id,
            old_invitee_id if old_pending_on == 'owner' else g.current_user.id,
        )
    entry.player2_id = None
    entry.player2 = None
    entry.partner_invitee_id = partner.id if partner else None
    entry.partner_invitee = partner
    entry.partner_status = 'pending' if partner else 'needed'
    entry.partner_pending_on = 'invitee' if partner else ''
    if partner:
        _notify_partner_invitation(tournament, entry, partner, 'invitee')
    db.session.commit()
    return jsonify(_detail_payload(tournament, g.current_user.id))


@tournaments_bp.post('/tournaments/<int:tournament_id>/entries/<int:entry_id>/partner-offer')
@rate_limit(30, 3600)
@login_required
def offer_tournament_partner(tournament_id, entry_id):
    """Offer to fill a visible partner-pool slot; the owner must consent."""
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'tournament_not_found'}), 404
    if tournament.status != 'registration':
        return jsonify({'error': 'registration_closed'}), 409
    if tournament.event_type != 'doubles':
        return jsonify({'error': 'not_doubles'}), 400
    if tournament.entry_for(g.current_user.id):
        return jsonify({'error': 'already_registered'}), 409
    existing_offer = tournament.partner_offer_for(g.current_user.id)
    if existing_offer:
        if existing_offer.id == entry_id:
            return jsonify(_detail_payload(tournament, g.current_user.id))
        return jsonify({'error': 'partner_offer_already_pending'}), 409
    entry = next((item for item in tournament.entries if item.id == entry_id), None)
    if not entry:
        return jsonify({'error': 'entry_not_found'}), 404
    if entry.partner_status != 'needed' or entry.player2_id:
        return jsonify({'error': 'partner_spot_unavailable'}), 409
    if entry.player1_id == g.current_user.id:
        return jsonify({'error': 'cannot_partner_self'}), 400
    if is_blocked_between(entry.player1_id, g.current_user.id):
        return jsonify({'error': 'blocked'}), 403
    division_error = _division_registration_error(tournament, g.current_user)
    if division_error:
        return jsonify({'error': division_error}), 409
    if not _partner_candidate_available(tournament, g.current_user.id):
        return jsonify({'error': 'partner_already_registered'}), 409
    entry.partner_invitee_id = g.current_user.id
    entry.partner_invitee = g.current_user
    entry.partner_status = 'pending'
    entry.partner_pending_on = 'owner'
    _notify_partner_invitation(tournament, entry, g.current_user, 'owner')
    db.session.commit()
    return jsonify(_detail_payload(tournament, g.current_user.id))


@tournaments_bp.post('/tournaments/<int:tournament_id>/partner/respond')
@rate_limit(30, 3600)
@login_required
def respond_tournament_partner(tournament_id):
    """Accept or decline the partner invitation/offer awaiting this user."""
    tournament = db.session.get(Tournament, tournament_id)
    if not tournament:
        return jsonify({'error': 'tournament_not_found'}), 404
    if tournament.status != 'registration':
        return jsonify({'error': 'registration_closed'}), 409
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload.get('accept'), bool):
        return jsonify({'error': 'accept_required'}), 400
    entry = tournament.partner_action_for(g.current_user.id)
    if not entry:
        return jsonify({'error': 'partner_action_not_pending'}), 409
    candidate = entry.partner_invitee
    owner = entry.player1
    if not candidate or not owner:
        return jsonify({'error': 'partner_action_not_pending'}), 409
    actor = owner if entry.partner_pending_on == 'invitee' else candidate
    _mark_partner_notifications_resolved(tournament, g.current_user.id, actor.id)
    if payload['accept']:
        for player in (owner, candidate):
            division_error = _division_registration_error(tournament, player)
            if division_error:
                return jsonify({
                    'error': division_error if player.id == g.current_user.id
                    else 'partner_' + division_error,
                }), 409
        if not _partner_candidate_available(
            tournament, candidate.id, exclude_entry=entry,
        ):
            return jsonify({'error': 'partner_already_registered'}), 409
        if is_blocked_between(owner.id, candidate.id):
            return jsonify({'error': 'blocked'}), 403
        entry.player2_id = candidate.id
        entry.player2 = candidate
        entry.partner_invitee_id = None
        entry.partner_invitee = None
        entry.partner_status = 'accepted'
        entry.partner_pending_on = ''
        other = owner if g.current_user.id == candidate.id else candidate
        notify(
            other.id,
            'tournament_join',
            f'{g.current_user.display_name} accepted — your team is entered in {tournament.name}',
            related_user_id=g.current_user.id,
            related_tournament_id=tournament.id,
            action_url=f'/#tournament/{tournament.id}',
        )
        if tournament.organizer_id not in {owner.id, candidate.id}:
            notify(
                tournament.organizer_id,
                'tournament_join',
                f'{owner.display_name} & {candidate.display_name} completed their team for {tournament.name}',
                related_user_id=g.current_user.id,
                related_tournament_id=tournament.id,
                action_url=f'/#tournament/{tournament.id}',
            )
    else:
        other = owner if g.current_user.id == candidate.id else candidate
        entry.partner_invitee_id = None
        entry.partner_invitee = None
        entry.partner_status = 'needed'
        entry.partner_pending_on = ''
        notify(
            other.id,
            'tournament_withdraw',
            f'{g.current_user.display_name} declined the partner request for {tournament.name}',
            related_user_id=g.current_user.id,
            related_tournament_id=tournament.id,
            action_url=f'/#tournament/{tournament.id}',
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
            f'{g.current_user.display_name} left your team in {tournament.name}',
            related_user_id=g.current_user.id,
            related_tournament_id=tournament.id,
        )
    elif entry.partner_invitee_id:
        pending = entry.partner_invitee or db.session.get(
            User, entry.partner_invitee_id,
        )
        if pending:
            notify(
                pending.id,
                'tournament_withdraw',
                f'{g.current_user.display_name} cancelled the partner request for {tournament.name}',
                related_user_id=g.current_user.id,
                related_tournament_id=tournament.id,
                action_url=f'/#tournament/{tournament.id}',
            )
            _mark_partner_notifications_resolved(
                tournament,
                pending.id if entry.partner_pending_on == 'invitee' else entry.player1_id,
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
    if entry.partner_invitee_id:
        notify(
            entry.partner_invitee_id,
            'tournament_withdraw',
            f'The organizer removed the pending team entry from {tournament.name}',
            related_user_id=g.current_user.id,
            related_tournament_id=tournament.id,
            action_url=f'/#tournament/{tournament.id}',
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
    if champion:
        award_new_badges(*champion.players())
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
    incomplete = [
        entry for entry in entries
        if not entry.partner_ready(tournament.event_type)
    ]
    if incomplete:
        return jsonify({
            'error': 'pending_partner_entries',
            'count': len(incomplete),
        }), 409
    outside_division = [
        player for entry in entries for player in entry.players()
        if _division_registration_error(tournament, player)
    ]
    if outside_division:
        return jsonify({
            'error': 'division_roster_changed',
            'count': len(outside_division),
        }), 409

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

    db.session.flush()
    _schedule_tournament_matches(tournament)
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
    games = match.game_scores()
    if len(games) > 1:
        scores = ', '.join(
            f'{game["score1"]}–{game["score2"]}' for game in games
        )
        return f'{left} vs {right}: {scores}'
    return f'{left} {match.score1}–{match.score2} {right}'


def _notify_score_submission(tournament, match, actor_id):
    participants = _match_participant_ids(tournament, match)
    eligible = _result_confirmer_ids(tournament, match)
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
    parsed_result = _parse_game_scores(payload, tournament)
    if not parsed_result:
        return jsonify({'error': 'invalid_score'}), 400
    score, games = parsed_result
    state = match.effective_result_state()
    if state not in ('unreported', 'disputed'):
        return jsonify({'error': 'result_not_reportable'}), 409
    if state == 'unreported' and (
            match.score1 is not None or match.score2 is not None
            or match.reported_by_id is not None):
        return jsonify({'error': 'result_not_reportable'}), 409

    now = utcnow()
    match.score1, match.score2 = score
    match.game_scores_json = json.dumps(games, separators=(',', ':'))
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
    match.review_reminded_at = None
    match.stall_alerted_at = None
    match.last_nudged_at = None
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
    if not _parse_game_scores({}, tournament, stored_match=match):
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
    match.stall_alerted_at = None
    _record_result_action(
        match, 'disputed', actor_id=user_id, reason=reason,
    )
    _notify_score_action(tournament, match, user_id, 'disputed', reason)
    return _commit_result_change(tournament_id, match_id)


@tournaments_bp.post('/tournaments/<int:tournament_id>/matches/<int:match_id>/nudge')
@rate_limit(30, 3600)
@login_required
def nudge_match_result(tournament_id, match_id):
    tournament, match, error = _result_request_context(tournament_id, match_id)
    if error:
        return error
    if tournament.organizer_id != g.current_user.id:
        return jsonify({'error': 'not_organizer'}), 403

    payload = _result_json_payload()
    version_error = _check_result_version(payload, tournament, match)
    if version_error:
        return version_error
    if match.effective_result_state() != 'awaiting_confirmation':
        return jsonify({'error': 'result_not_awaiting_confirmation'}), 409

    targets = _result_confirmer_ids(tournament, match) - {g.current_user.id}
    if not targets:
        return jsonify({'error': 'not_allowed'}), 409
    now = utcnow()
    cooldown = _result_nudge_cooldown()
    available_at = (
        match.last_nudged_at + cooldown if match.last_nudged_at else now
    )
    if available_at > now:
        data = _detail_payload(tournament, g.current_user.id)
        data.update({
            'already_sent': True,
            'retry_after_seconds': max(
                1, int((available_at - now).total_seconds() + 0.999),
            ),
        })
        return jsonify(data)

    bucket_seconds = max(60, int(cooldown.total_seconds()))
    _notify_result_users(
        tournament,
        match,
        targets,
        f'{tournament.name}: please confirm the submitted score',
        _score_summary(tournament, match),
        actor_id=g.current_user.id,
        unread_dedupe_key=(
            f'tournament-result-nudge:{match.id}:v{match.result_version}:'
            f'{int(now.timestamp() // bucket_seconds)}'
        ),
    )
    match.last_nudged_at = now
    db.session.commit()
    data = _detail_payload(tournament, g.current_user.id)
    data.update({'already_sent': False, 'retry_after_seconds': bucket_seconds})
    return jsonify(data)


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
    parsed_result = _parse_game_scores(payload, tournament, stored_match=match)
    if not parsed_result:
        status = 400 if {'score1', 'score2', 'games'} & payload.keys() else 409
        return jsonify({'error': 'invalid_score' if status == 400 else 'score_missing'}), status
    score, games = parsed_result

    user_id = g.current_user.id
    match.score1, match.score2 = score
    match.game_scores_json = json.dumps(games, separators=(',', ':'))
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


@tournaments_bp.post('/tournaments/<int:tournament_id>/matches/<int:match_id>/forfeit')
@rate_limit(60, 3600)
@login_required
def record_match_forfeit(tournament_id, match_id):
    """Advance a ready match when the organizer records one side as absent."""
    tournament, match, error = _result_request_context(tournament_id, match_id)
    if error:
        return error
    if tournament.organizer_id != g.current_user.id:
        return jsonify({'error': 'not_organizer'}), 403

    payload = _result_json_payload()
    version_error = _check_result_version(payload, tournament, match)
    if version_error:
        return version_error
    state = match.effective_result_state()
    if state not in ('unreported', 'awaiting_confirmation', 'disputed'):
        return jsonify({'error': 'result_not_forfeitable'}), 409

    raw_entry_id = payload.get('forfeiting_entry_id')
    if isinstance(raw_entry_id, bool):
        return jsonify({'error': 'invalid_forfeiting_entry'}), 400
    try:
        forfeiting_entry_id = int(raw_entry_id)
    except (TypeError, ValueError):
        return jsonify({'error': 'invalid_forfeiting_entry'}), 400
    if forfeiting_entry_id not in (match.entry1_id, match.entry2_id):
        return jsonify({'error': 'invalid_forfeiting_entry'}), 400

    winner_entry_id = (
        match.entry2_id
        if forfeiting_entry_id == match.entry1_id else match.entry1_id
    )
    entries = {entry.id: entry for entry in tournament.entries}
    forfeiting_entry = entries.get(forfeiting_entry_id)
    winner_entry = entries.get(winner_entry_id)
    reason = str(payload.get('reason') or '').strip()[:500] or (
        f'{forfeiting_entry.display_name() if forfeiting_entry else "Entry"} '
        'did not play'
    )

    now = utcnow()
    match.score1 = None
    match.score2 = None
    match.game_scores_json = '[]'
    match.winner_entry_id = winner_entry_id
    match.winner_entry = winner_entry
    match.result_state = 'confirmed'
    match.confirmed_by_id = g.current_user.id
    match.confirmed_by = g.current_user
    match.confirmed_at = now
    match.dispute_reason = reason
    match.resolution_kind = 'organizer_forfeit'
    match.review_reminded_at = None
    match.stall_alerted_at = None
    _record_result_action(
        match, 'resolved', actor_id=g.current_user.id, reason=reason,
    )

    if tournament.format == 'single_elim':
        _advance_winner(tournament, match, tournament.total_rounds())
        _propagate_bye_wins(tournament)
    elif all(candidate.winner_entry_id is not None for candidate in tournament.matches):
        _complete_tournament(
            tournament, _top_of_standings(tournament), source_match=match,
        )

    _notify_result_users(
        tournament,
        match,
        _match_participant_ids(tournament, match) | {tournament.organizer_id},
        f'{tournament.name}: match recorded as a forfeit',
        reason,
        actor_id=g.current_user.id,
    )
    return _commit_result_change(tournament_id, match_id)


def _top_of_standings(tournament):
    table = _standings(tournament)
    return table[0]['entry']['id'] if table else None


def maintain_tournament_results(now=None):
    """Remind reviewers, auto-confirm quiet scores, and flag stale disputes."""
    now = now or utcnow()
    window = timedelta(hours=_tournament_result_window_hours())
    half_cutoff = now - (window / 2)
    candidate_ids = (
        db.session.query(TournamentMatch.tournament_id, TournamentMatch.id)
        .join(Tournament, Tournament.id == TournamentMatch.tournament_id)
        .filter(
            Tournament.status == 'active',
            TournamentMatch.result_state.in_([
                'awaiting_confirmation', 'disputed',
            ]),
            TournamentMatch.reported_at.is_not(None),
            TournamentMatch.reported_at <= half_cutoff,
        )
        .order_by(TournamentMatch.reported_at.asc(), TournamentMatch.id.asc())
        .limit(200)
        .all()
    )
    # Candidate discovery must not hold a read transaction while row locks are
    # acquired one competition at a time.
    db.session.rollback()
    outcomes = {'reminded': 0, 'auto_confirmed': 0, 'stalled': 0}

    for tournament_id, match_id in candidate_ids:
        tournament, match = _locked_tournament_match(tournament_id, match_id)
        if not tournament or not match or tournament.status != 'active':
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
                    if not _parse_game_scores({}, tournament, stored_match=match):
                        db.session.rollback()
                        continue
                    match.result_state = 'confirmed'
                    match.confirmed_by_id = None
                    match.confirmed_by = None
                    match.confirmed_at = now
                    match.resolution_kind = 'automatic_timeout'
                    _record_result_action(match, 'auto_confirmed')
                    _progress_confirmed_result(tournament, match)
                    _notify_score_action(
                        tournament, match, None, 'confirmed automatically',
                    )
                    outcomes['auto_confirmed'] += 1
                elif (
                    match.reported_at + (window / 2) <= now
                    and match.review_reminded_at is None
                ):
                    targets = _result_confirmer_ids(tournament, match)
                    _notify_result_users(
                        tournament,
                        match,
                        targets,
                        f'{tournament.name}: this score still needs your confirmation',
                        _score_summary(tournament, match),
                        unread_dedupe_key=(
                            f'tournament-result-half:{match.id}:'
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
                _notify_result_users(
                    tournament,
                    match,
                    {tournament.organizer_id},
                    f'{tournament.name}: a disputed score needs your decision',
                    _score_summary(tournament, match),
                    unread_dedupe_key=(
                        f'tournament-result-stall:{match.id}:'
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
            # A manual result action may win on SQLite between candidate read
            # and commit. The immutable event/version key makes that harmless.
            db.session.rollback()
            continue
    return outcomes


REMINDER_LEAD_MINUTES = 65


def send_tournament_reminders():
    """Scheduled hour-before/day-before nudges, at most once per tournament."""
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
    ready_to_start = Tournament.query.filter(
        Tournament.status == 'registration',
        Tournament.starts_at <= now,
    ).all()

    changed = False
    for tournament, title in (
        [(t, f'{t.name} starts in about an hour — tap I’m here when you arrive')
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
    for tournament in ready_to_start:
        if sum(
            entry.partner_ready(tournament.event_type)
            for entry in tournament.entries
        ) < MIN_ENTRIES:
            continue
        title = f'{tournament.name} is ready for you to start'
        if Notification.query.filter_by(
            user_id=tournament.organizer_id,
            kind='tournament_reminder',
            related_tournament_id=tournament.id,
            title=title,
        ).first():
            continue
        notify(
            tournament.organizer_id,
            'tournament_reminder',
            title,
            'Review the field, then build the bracket when everyone is ready.',
            related_tournament_id=tournament.id,
            action_url=f'/#tournament/{tournament.id}',
            unread_dedupe_key=f'tournament-ready-to-start:{tournament.id}',
        )
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
    return jsonify(_summary_payload(tournament, g.current_user.id))
