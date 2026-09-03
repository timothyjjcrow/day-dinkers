"""Validation and serialization for durable player-invite destinations."""

from __future__ import annotations

from backend.app import db
from backend.models import Game, iso, utcnow


def invite_destination_game(inviter_id, raw_game_id):
    """Return a safe, live game destination owned by an invitation sender.

    Player invite URLs are public and enumerable, so they may only disclose an
    ordinary open scheduled game. Private/friends-only games, live rallies,
    completed/cancelled rows, stale starts, and games unrelated to the inviter
    all degrade to an inviter-only link.
    """
    if raw_game_id in (None, '') or isinstance(raw_game_id, bool):
        return None
    try:
        game_id = int(raw_game_id)
        normalized_inviter_id = int(inviter_id)
    except (TypeError, ValueError):
        return None
    if game_id <= 0 or normalized_inviter_id <= 0:
        return None
    game = db.session.get(Game, game_id)
    if not game:
        return None
    if (
        game.status != 'upcoming'
        or game.visibility != 'open'
        or bool(game.is_instant)
        or not game.scheduled_at
        or game.scheduled_at <= utcnow()
    ):
        return None
    if game.creator_id != normalized_inviter_id and not any(
        player.user_id == normalized_inviter_id for player in game.players
    ):
        return None
    return game


def invite_destination_payload(inviter_id, raw_game_id):
    """Public, non-sensitive destination context for auth and share screens."""
    game = invite_destination_game(inviter_id, raw_game_id)
    if not game:
        return None
    return {
        'kind': 'game',
        'id': game.id,
        'action_url': f'/#game/{game.id}',
        'scheduled_at': iso(game.scheduled_at),
        'court_name': game.court.name if game.court else '',
        'session_kind': 'match' if game.game_type == 'ranked' else 'session',
    }
