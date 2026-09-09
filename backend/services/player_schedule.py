"""Shared personal commitment checks; court occupancy is not a reservation."""
import hashlib
import hmac
import json
from datetime import timedelta

from sqlalchemy import or_

from backend.app import db
from backend.models import (
    Game, GamePlayer, League, LeagueMatch, Tournament, TournamentEntry,
    TournamentMatch, User, iso, utcnow,
)


def player_schedule_conflicts(user_ids, start, duration_minutes, *, viewer_id=None,
                              exclude_game_id=None, exclude_game_ids=None, exclude_league_match_id=None,
                              exclude_tournament_id=None, exclude_tournament_match_id=None, all_future=False):
    """Return intersecting accepted plans, with other players' details withheld.

    Unknown session duration uses a labeled 90-minute estimate. Unaccepted
    invitations, queued/offered places, proposed league times, completed games
    and whole tournament envelopes are not appointments to play.
    """
    wanted = {int(user_id) for user_id in user_ids if user_id}
    if not wanted or start is None:
        return []
    end = None if all_future else start + timedelta(minutes=duration_minutes or 90)
    rows = []
    excluded_games = set(exclude_game_ids or ()) | {exclude_game_id}
    names = dict(db.session.query(User.id, User.display_name).filter(User.id.in_(wanted)).all())

    def append(kind, record_id, participants, when, minutes, title, url, confidence, version=0):
        if not when:
            return
        until = when + timedelta(minutes=minutes)
        if (end is not None and when >= end) or until <= start:
            return
        for user_id in wanted.intersection(participants):
            fingerprint = hashlib.sha256(json.dumps([
                kind, record_id, user_id, iso(when), iso(until), confidence, version,
            ], separators=(',', ':')).encode()).hexdigest()
            own = user_id == viewer_id
            rows.append({
                'user_id': user_id, 'version': fingerprint,
                'player_name': names.get(user_id, 'Player'),
                'kind': kind if own else 'busy',
                'title': title if own else 'Another commitment',
                'starts_at': iso(when) if own else None,
                'ends_at': iso(until) if own else None,
                'action_url': url if own else None,
                'timing': confidence,
            })

    games = Game.query.join(GamePlayer).filter(
        GamePlayer.user_id.in_(wanted), Game.status == 'upcoming',
        *([] if end is None else [Game.scheduled_at < end]),
    ).distinct().all()
    for game in games:
        if game.id in excluded_games:
            continue
        append('game', game.id, {p.user_id for p in game.players}, game.scheduled_at,
               game.duration_minutes or 90, game.title or 'Pickleball session',
               f'/#game/{game.id}', 'scheduled' if game.duration_minutes else 'estimated',
               iso(game.updated_at))

    leagues = LeagueMatch.query.join(League).filter(
        League.status == 'active', LeagueMatch.round == League.current_round,
        *([] if end is None else [LeagueMatch.scheduled_at < end]),
        or_(LeagueMatch.player1_id.in_(wanted), LeagueMatch.player2_id.in_(wanted)),
    ).all()
    for match in leagues:
        if match.id == exclude_league_match_id or match.effective_result_state() != 'unreported':
            continue
        participants = {user_id for user_id in (match.player1_id, match.player2_id)
                        if match.league.member_for(user_id)
                        and not match.league.member_for(user_id).withdrawn_at}
        append('league_match', match.id, participants, match.scheduled_at,
               match.scheduled_duration_minutes or 60, match.league.name,
               f'/#league/{match.league_id}/match/{match.id}', 'scheduled', match.schedule_version)

    entry_ids = db.session.query(TournamentEntry.id).filter(or_(
        TournamentEntry.player1_id.in_(wanted), TournamentEntry.player2_id.in_(wanted)))
    tournaments = TournamentMatch.query.join(Tournament).filter(
        Tournament.status.in_(['registration', 'active']),
        *([] if end is None else [or_(TournamentMatch.scheduled_at < end, TournamentMatch.started_at < end, TournamentMatch.called_at < end)]),
        or_(TournamentMatch.entry1_id.in_(entry_ids), TournamentMatch.entry2_id.in_(entry_ids)),
    ).all()
    for match in tournaments:
        if (match.id == exclude_tournament_match_id or match.tournament_id == exclude_tournament_id
                or match.effective_result_state() != 'unreported'):
            continue
        participants = {user_id for entry in (match.entry1, match.entry2) if entry
                        for user_id in (entry.player1_id, entry.player2_id) if user_id}
        when = (match.started_at if match.play_state == 'playing' and match.started_at
                else match.called_at if match.play_state == 'called' and match.called_at else match.scheduled_at)
        append('tournament_match', match.id, participants, when,
               match.tournament.match_minutes or 30, match.tournament.name,
               f'/#tournament/{match.tournament_id}/match/{match.id}',
               'estimated' if match.play_state == 'estimated' else match.play_state or 'estimated',
               match.tournament.schedule_version)
    return sorted(rows, key=lambda row: (row['starts_at'] or '', row['user_id'], row['version']))


def personal_schedule_overlaps(user_id, now=None):
    """All future conflicts, independent of the visible agenda page."""
    rows = player_schedule_conflicts([user_id], now or utcnow(), None, viewer_id=user_id, all_future=True)
    active, overlaps = [], []
    for row in rows:
        active = [prior for prior in active if prior['ends_at'] > row['starts_at']]
        for prior in active:
            overlaps.append({'plans': [prior, row],
                             'starts_at': max(prior['starts_at'], row['starts_at']),
                             'ends_at': min(prior['ends_at'], row['ends_at']),
                             'estimated': 'estimated' in (prior['timing'], row['timing'])})
        active.append(row)
    return overlaps


def schedule_review_needed(user_ids, start, duration_minutes, payload, *, scope, viewer_id=None, **exclusions):
    """Require an explicit acknowledgement of the current conflict snapshot."""
    conflicts = player_schedule_conflicts(user_ids, start, duration_minutes, viewer_id=viewer_id, **exclusions)
    if not conflicts:
        return None
    token = hashlib.sha256(json.dumps([
        scope, iso(start), duration_minutes, [row['version'] for row in conflicts],
    ], separators=(',', ':')).encode()).hexdigest()
    acknowledged = payload.get('schedule_conflict_ack') if isinstance(payload, dict) else None
    if isinstance(acknowledged, str) and hmac.compare_digest(token, acknowledged):
        return None
    return {'error': 'schedule_conflict', 'conflicts': conflicts,
            'schedule_conflict_token': token, 'proposed_start': iso(start),
            'proposed_duration_minutes': duration_minutes}


def schedule_batch_review_needed(plans, payload, *, scope, viewer_id=None):
    """Review a multi-slot change as one snapshot, without retry-token loops.

    Each plan supplies user_ids, start, duration_minutes and optional exclusions.
    The fingerprint includes every proposed slot, even one currently free.
    """
    snapshots, conflicts = [], []
    for plan in plans:
        start, minutes = plan['start'], plan['duration_minutes']
        users = sorted({int(uid) for uid in plan['user_ids'] if uid})
        exclusions = plan.get('exclusions', {})
        found = player_schedule_conflicts(users, start, minutes, viewer_id=viewer_id, **exclusions)
        snapshots.append([users, iso(start), minutes, exclusions, [row['version'] for row in found]])
        conflicts.extend({**row, 'proposed_start': iso(start)} for row in found)
    if not conflicts:
        return None
    token = hashlib.sha256(json.dumps([scope, snapshots], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    acknowledged = payload.get('schedule_conflict_ack') if isinstance(payload, dict) else None
    if isinstance(acknowledged, str) and hmac.compare_digest(token, acknowledged):
        return None
    return {'error': 'schedule_conflict', 'conflicts': conflicts,
            'schedule_conflict_token': token, 'proposed_slot_count': len(plans)}
