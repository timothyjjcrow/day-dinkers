"""Portable, explicitly scoped personal records; never serialize auth secrets."""
import json
from datetime import date, datetime
from sqlalchemy import or_
from sqlalchemy.orm import lazyload

from backend.models import (
    CheckIn, ClubMember, CompetitionResultEvent, CrewMember, FavoriteCourt, Game,
    GameInvite, GamePlayer, GameSessionAttendance, LeagueMatch, LeagueMember,
    Message, TournamentEntry, TournamentMatch, iso, utcnow,
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=lambda value: (
        iso(value) if isinstance(value, datetime) else value.isoformat()
        if isinstance(value, date) else str(value)))


def _stored_json(value, fallback=None):
    try:
        return json.loads(value or '[]')
    except (ValueError, TypeError):
        return [] if fallback is None else fallback


def _court(court):
    return {'id': court.id, 'name': court.name, 'city': court.city,
            'state': court.state} if court else None


def _player(player):
    return {'id': player.id, 'name': 'Deleted player' if player.deleted_at
            else player.display_name} if player else None


def _competition(competition):
    return {'id': competition.id, 'name': competition.name, 'status': competition.status,
            'starts_at': competition.starts_at, 'court': _court(competition.court)}


def _session(game, user_id):
    place = next((row for row in game.players if row.user_id == user_id), None)
    # A former RSVP still has its own dated attendance record. It does not gain
    # access to the current roster or a result played after that player left.
    result = None
    if place and (game.score_team1 is not None or game.score_history != '[]'):
        result = {'score_team1': game.score_team1, 'score_team2': game.score_team2,
            'games': [row.to_dict() for row in game.score_lines],
            'your_team': place.team, 'your_rating_change': place.rating_delta,
            'confirmation_kind': game.score_confirmation_kind,
            'completed_at': game.completed_at, 'version': game.score_version,
            'history': _stored_json(game.score_history)}
    return {'id': game.id, 'title': game.title or ('Ranked match' if game.game_type == 'ranked' else 'Play session'),
        'type': game.game_type, 'status': game.status, 'court': _court(game.court),
        'scheduled_at': game.scheduled_at, 'duration_minutes': game.duration_minutes,
        'cost_cents': game.cost_cents, 'court_number': game.court_number,
        'hosted_by_you': game.creator_id == user_id, 'result': result}


def _league_match(match):
    return {'id': match.id, 'league': _competition(match.league), 'round': match.round,
        'group_number': match.box, 'player1': _player(match.player1), 'player2': _player(match.player2),
        'scheduled_at': match.scheduled_at, 'court': _court(match.scheduled_court or match.league.court),
        'duration_minutes': match.scheduled_duration_minutes, 'score1': match.score1,
        'score2': match.score2, 'winner_id': match.winner_id,
        'result_state': match.effective_result_state(), 'result_version': match.result_version,
        'reported_at': match.reported_at, 'confirmed_at': match.confirmed_at}


def _tournament_side(entry):
    return {'entry_id': entry.id, 'players': [_player(player) for player in entry.players()]} if entry else None


def _tournament_match(match):
    return {'id': match.id, 'tournament': _competition(match.tournament), 'round': match.round,
        'side1': _tournament_side(match.entry1), 'side2': _tournament_side(match.entry2),
        'scheduled_at': match.scheduled_at, 'court_number': match.court_number,
        'score1': match.score1, 'score2': match.score2, 'games': match.game_scores(),
        'winner_entry_id': match.winner_entry_id, 'result_state': match.effective_result_state(),
        'result_version': match.result_version, 'reported_at': match.reported_at,
        'confirmed_at': match.confirmed_at}


def player_data_export(user):
    """Yield one JSON file without loading all authored photos into memory."""
    profile_fields = (
        'id', 'display_name', 'email', 'email_verified_at', 'created_at', 'bio',
        'skill_level', 'skill_rating', 'dupr_rating', 'dupr_id', 'avatar_color',
        'avatar_url', 'avatar_data', 'home_court_id', 'home_area', 'home_lat', 'home_lng',
        'nearby_visibility', 'away_until', 'rating', 'ranked_wins', 'ranked_losses',
    )
    profile = {key: getattr(user, key) for key in profile_fields}
    profile['availability'] = user.availability_list()
    profile['muted_notifications'] = sorted(user.muted_kinds())
    yield '{"format":"third-shot-player-data-v1","exported_at":' + _json(utcnow())
    yield ',"scope":' + _json({
        'included': ['Profile and photo', 'Usual times and preferences', 'Saved courts and check-ins',
                     'Your dated sessions, attendance and ranked result history',
                     'Your league and tournament entries, matches and result decisions', 'Group memberships',
                     'Messages and photos you sent'],
        'not_included': ['Other players\u2019 messages', 'Sign-in secrets and private access tokens',
                         'Organization records and operational moderation logs'],
    })
    yield ',"profile":' + _json(profile)
    collections = (
        ('saved_courts', FavoriteCourt, 'user_id', ('court_id', 'created_at')),
        ('check_ins', CheckIn, 'user_id', ('court_id', 'checked_in_at', 'checked_out_at',
                                         'looking_for_game', 'location_verified_at')),
        ('session_places', GamePlayer, 'user_id', ('game_id', 'team', 'rating_delta', 'created_at')),
        ('session_invitations', GameInvite, 'user_id', ('game_id', 'created_at')),
        ('attendance', GameSessionAttendance, 'user_id', ('game_id', 'rsvp_status', 'rsvp_joined_at',
            'rsvp_left_at', 'attended', 'recorded_at', 'history')),
        ('public_group_memberships', ClubMember, 'user_id', ('club_id', 'role', 'notification_level', 'created_at')),
        ('private_group_memberships', CrewMember, 'user_id', ('crew_id', 'created_at')),
        ('sent_messages', Message, 'sender_id', ('id', 'recipient_id', 'court_id', 'game_id',
            'tournament_id', 'club_id', 'crew_id', 'league_id', 'body', 'image_data', 'reply_to_id', 'created_at')),
    )
    for name, model, owner_field, fields in collections:
        yield ',' + _json(name) + ':['
        separator = ''
        for row in model.query.filter(getattr(model, owner_field) == user.id).order_by(model.id).yield_per(100):
            record = {key: getattr(row, key) for key in fields}
            if name == 'attendance': record['history'] = _stored_json(row.history)
            if name == 'saved_courts': record['court'] = _court(row.court)
            if name == 'check_ins': record['court'] = _court(row.court)
            if name == 'public_group_memberships': record['name'] = row.club.name
            if name == 'private_group_memberships': record['name'] = row.crew.name
            if name == 'session_invitations':
                record['session'] = _session(row.game, user.id)
            # A quoted message belongs to its author, not the replying player.
            # Authored originals appear once in sent_messages; never copy quotes.
            yield separator + _json(record)
            separator = ','
        yield ']'

    own_games = GamePlayer.query.with_entities(GamePlayer.game_id).filter_by(user_id=user.id)
    own_attendance = GameSessionAttendance.query.with_entities(GameSessionAttendance.game_id).filter_by(user_id=user.id)
    league_matches = LeagueMatch.query.filter(or_(LeagueMatch.player1_id == user.id, LeagueMatch.player2_id == user.id))
    own_entries = TournamentEntry.query.with_entities(TournamentEntry.id).filter(
        or_(TournamentEntry.player1_id == user.id, TournamentEntry.player2_id == user.id))
    tournament_matches = TournamentMatch.query.filter(or_(TournamentMatch.entry1_id.in_(own_entries), TournamentMatch.entry2_id.in_(own_entries)))
    readable_collections = (
        ('sessions', Game.query.filter(or_(Game.creator_id == user.id, Game.id.in_(own_games), Game.id.in_(own_attendance))),
         lambda row: _session(row, user.id)),
        ('league_memberships', LeagueMember.query.filter_by(user_id=user.id),
         lambda row: {'league': _competition(row.league), 'group_number': row.box,
             'points': row.points, 'wins': row.wins, 'losses': row.losses,
             'joined_at': row.created_at, 'withdrawn_at': row.withdrawn_at,
             'availability_history': _stored_json(row.availability_history)}),
        ('league_matches', league_matches, _league_match),
        ('tournament_entries', TournamentEntry.query.filter(or_(TournamentEntry.player1_id == user.id,
            TournamentEntry.player2_id == user.id, TournamentEntry.partner_invitee_id == user.id)),
         lambda row: {'entry_id': row.id, 'tournament': _competition(row.tournament),
             'players': [_player(player) for player in row.players()], 'partner_status': row.partner_status,
             'your_partner_invitation': row.partner_invitee_id == user.id, 'seed': row.seed,
             'joined_at': row.created_at, 'your_arrived_at': row.player1_arrived_at
                if row.player1_id == user.id else row.player2_arrived_at if row.player2_id == user.id else None}),
        ('tournament_matches', tournament_matches, _tournament_match),
        ('competition_result_history', CompetitionResultEvent.query.filter(or_(
            (CompetitionResultEvent.competition_type == 'league') & CompetitionResultEvent.match_id.in_(league_matches.with_entities(LeagueMatch.id)),
            (CompetitionResultEvent.competition_type == 'tournament') & CompetitionResultEvent.match_id.in_(tournament_matches.with_entities(TournamentMatch.id)))),
         lambda row: row.to_dict(include_reason=True)),
    )
    for name, query, serialize in readable_collections:
        yield ',' + _json(name) + ':['
        separator = ''
        # Don't eagerly materialize the entire competition roster for an export
        # that includes only the requesting player's entries and matches.
        for row in query.options(lazyload('*')).order_by(query.column_descriptions[0]['entity'].id).yield_per(100):
            yield separator + _json(serialize(row))
            separator = ','
        yield ']'
    yield '}'
