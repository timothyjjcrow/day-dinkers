"""Ranked competitive play — shared helpers and constants."""
from datetime import datetime, timedelta
from backend.app import db, socketio
from backend.models import (
    User, Match, MatchPlayer, RankedQueue, CheckIn, Notification,
    RankedLobby, RankedLobbyPlayer,
)
from backend.services.elo import calculate_elo_changes
from backend.time_utils import utcnow_naive

_ALLOWED_MATCH_TYPES = {'singles', 'doubles'}
_ALLOWED_LOBBY_SOURCES = {
    'queue',
    'court_challenge',
    'scheduled_challenge',
    'friends_challenge',
    'leaderboard_challenge',
    'manual',
}
_MIN_SCORE = 0
_MAX_SCORE = 99
_LOBBY_EXPIRY_HOURS = 48
_MATCH_EXPIRY_HOURS = 24


def _coerce_bool(raw_value):
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, (int, float)):
        return raw_value == 1
    if raw_value is None:
        return False
    return str(raw_value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _parse_team_ids(raw_ids):
    if not isinstance(raw_ids, list):
        return None
    ids = []
    for raw in raw_ids:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        ids.append(value)
    return ids


def _validate_match_setup(match_type, team1_ids, team2_ids):
    if match_type not in _ALLOWED_MATCH_TYPES:
        return 'Invalid match type'
    if not team1_ids or not team2_ids:
        return 'Both teams must have players'

    expected_per_team = 1 if match_type == 'singles' else 2
    if len(team1_ids) != expected_per_team or len(team2_ids) != expected_per_team:
        return f'{match_type.capitalize()} requires {expected_per_team} per team'

    all_ids = team1_ids + team2_ids
    if len(set(all_ids)) != len(all_ids):
        return 'Duplicate players across teams'
    return None


def _resolve_players(user_ids):
    unique_ids = set(user_ids)
    players = User.query.filter(User.id.in_(unique_ids)).all()
    if len(players) != len(unique_ids):
        return None
    return {player.id: player for player in players}


def _create_match_record(court_id, match_type, team1_ids, team2_ids, players_by_id):
    match = Match(
        court_id=court_id,
        match_type=match_type,
        status='in_progress',
    )
    db.session.add(match)
    db.session.flush()

    for team_num, team_ids in [(1, team1_ids), (2, team2_ids)]:
        for uid in team_ids:
            db.session.add(MatchPlayer(
                match_id=match.id,
                user_id=uid,
                team=team_num,
                elo_before=players_by_id[uid].elo_rating,
            ))
    return match


def _checked_in_user_ids(court_id, user_ids=None):
    query = CheckIn.query.filter_by(court_id=court_id, checked_out_at=None)
    if user_ids is not None:
        query = query.filter(CheckIn.user_id.in_(list(user_ids)))
    return {ci.user_id for ci in query.all()}


def _user_checked_in_at_court(user_id, court_id):
    active = CheckIn.query.filter_by(user_id=user_id, checked_out_at=None).first()
    return bool(active and active.court_id == court_id)


def _update_lobby_status(lobby):
    statuses = [p.acceptance_status for p in (lobby.players or [])]
    if any(status == 'declined' for status in statuses):
        lobby.status = 'declined'
        return
    if statuses and all(status == 'accepted' for status in statuses):
        lobby.status = 'ready'
        return
    lobby.status = 'pending_acceptance'


def _lobby_to_dict(lobby):
    data = lobby.to_dict()
    accepted_count = 0
    for player in data.get('players', []):
        if player.get('acceptance_status') == 'accepted':
            accepted_count += 1
    data['accepted_count'] = accepted_count
    data['total_players'] = len(data.get('players', []))
    return data


def _create_lobby(
    *,
    court_id,
    created_by_id,
    match_type,
    team1_ids,
    team2_ids,
    source,
    scheduled_for=None,
    accepted_user_ids=None,
):
    lobby = RankedLobby(
        court_id=court_id,
        created_by_id=created_by_id,
        match_type=match_type,
        source=source if source in _ALLOWED_LOBBY_SOURCES else 'manual',
        scheduled_for=scheduled_for,
        status='pending_acceptance',
    )
    db.session.add(lobby)
    db.session.flush()

    accepted_set = set(accepted_user_ids or [])
    now = utcnow_naive()

    for team_num, team_ids in [(1, team1_ids), (2, team2_ids)]:
        for uid in team_ids:
            accepted = uid in accepted_set
            db.session.add(RankedLobbyPlayer(
                lobby_id=lobby.id,
                user_id=uid,
                team=team_num,
                acceptance_status='accepted' if accepted else 'pending',
                responded_at=now if accepted else None,
            ))

    db.session.flush()
    _update_lobby_status(lobby)
    return lobby


def _notify_lobby_players(lobby, notif_type, content, exclude_user_ids=None):
    excluded = set(exclude_user_ids or [])
    for participant in lobby.players:
        if participant.user_id in excluded:
            continue
        db.session.add(Notification(
            user_id=participant.user_id,
            notif_type=notif_type,
            content=content,
            reference_id=lobby.id,
        ))


def _parse_iso_datetime(raw_value):
    raw = str(raw_value or '').strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return None


def _prune_queue_for_court(court_id):
    active_subquery = db.session.query(CheckIn.user_id).filter(
        CheckIn.court_id == court_id,
        CheckIn.checked_out_at.is_(None),
    )
    RankedQueue.query.filter(
        RankedQueue.court_id == court_id,
        ~RankedQueue.user_id.in_(active_subquery),
    ).delete(synchronize_session=False)


def _emit_ranked_update(court_id=None, reason=''):
    socketio.emit('ranked_update', {
        'court_id': court_id,
        'reason': reason,
        'updated_at': utcnow_naive().isoformat(),
    })


def _emit_notification_update(court_id=None, reason=''):
    socketio.emit('notification_update', {
        'court_id': court_id,
        'reason': reason,
        'updated_at': utcnow_naive().isoformat(),
    })


def _apply_elo(match):
    """Calculate and apply ELO changes to all players in the match."""
    team1_mps = [mp for mp in match.players if mp.team == 1]
    team2_mps = [mp for mp in match.players if mp.team == 2]

    team1_data = [{
        'elo_rating': mp.elo_before or mp.user.elo_rating,
        'games_played': mp.user.games_played,
    } for mp in team1_mps]

    team2_data = [{
        'elo_rating': mp.elo_before or mp.user.elo_rating,
        'games_played': mp.user.games_played,
    } for mp in team2_mps]

    team1_changes, team2_changes = calculate_elo_changes(
        team1_data, team2_data,
        match.team1_score, match.team2_score,
    )

    for team_mps, changes, team_num in [
        (team1_mps, team1_changes, 1),
        (team2_mps, team2_changes, 2),
    ]:
        for mp, change in zip(team_mps, changes):
            mp.elo_after = (mp.elo_before or mp.user.elo_rating) + change
            mp.elo_change = change
            mp.user.elo_rating = mp.elo_after
            mp.user.games_played += 1
            if match.winner_team == team_num:
                mp.user.wins += 1
            else:
                mp.user.losses += 1


def _expire_stale_items(court_id=None):
    """Mark stale lobbies as expired and old in-progress matches as cancelled."""
    now = utcnow_naive()
    lobby_cutoff = now - timedelta(hours=_LOBBY_EXPIRY_HOURS)
    match_cutoff = now - timedelta(hours=_MATCH_EXPIRY_HOURS)

    lobby_query = RankedLobby.query.filter(
        RankedLobby.status.in_(['pending_acceptance', 'ready']),
        RankedLobby.created_at < lobby_cutoff,
    )
    if court_id:
        lobby_query = lobby_query.filter(RankedLobby.court_id == court_id)
    lobby_query.update({'status': 'expired'}, synchronize_session=False)

    match_query = Match.query.filter(
        Match.status == 'in_progress',
        Match.created_at < match_cutoff,
    )
    if court_id:
        match_query = match_query.filter(Match.court_id == court_id)
    match_query.update({'status': 'cancelled'}, synchronize_session=False)


def _categorize_court_lobbies(court_id):
    """Return (ready, scheduled, pending) lobby lists for a court."""
    all_lobbies = RankedLobby.query.filter(
        RankedLobby.court_id == court_id,
        RankedLobby.status.in_(['pending_acceptance', 'ready']),
    ).order_by(
        RankedLobby.scheduled_for.asc(),
        RankedLobby.created_at.desc(),
    ).all()

    ready, scheduled, pending = [], [], []
    for lobby in all_lobbies:
        data = _lobby_to_dict(lobby)
        if lobby.status == 'ready' and lobby.scheduled_for:
            scheduled.append(data)
        elif lobby.status == 'ready':
            ready.append(data)
        else:
            pending.append(data)
    return ready, scheduled, pending
