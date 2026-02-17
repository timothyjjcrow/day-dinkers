"""Tournament helpers for ranked tournament lifecycle and bracket progression."""
import math
from backend.app import db
from backend.models import (
    User, Match, MatchPlayer, Notification,
    TournamentResult, TournamentParticipant,
)
from backend.time_utils import utcnow_naive

ALLOWED_TOURNAMENT_FORMATS = {'single_elimination'}
ALLOWED_TOURNAMENT_ACCESS = {'open', 'invite_only'}
ALLOWED_NO_SHOW_POLICIES = {'auto_forfeit', 'host_mark'}
ALLOWED_TOURNAMENT_STATUSES = {'upcoming', 'live', 'completed', 'cancelled'}


def is_power_of_two(value):
    if not isinstance(value, int) or value <= 0:
        return False
    return (value & (value - 1)) == 0


def total_rounds_for_size(bracket_size):
    if not is_power_of_two(bracket_size):
        return None
    return int(math.log2(bracket_size))


def winner_user_id(match):
    if not match or match.winner_team not in (1, 2):
        return None
    row = next((p for p in (match.players or []) if p.team == match.winner_team), None)
    return row.user_id if row else None


def loser_user_id(match):
    if not match or match.winner_team not in (1, 2):
        return None
    loser_team = 2 if match.winner_team == 1 else 1
    row = next((p for p in (match.players or []) if p.team == loser_team), None)
    return row.user_id if row else None


def should_apply_elo_for_match(match):
    """Tournament matches can opt out of ELO; all other matches keep current behavior."""
    if not match or not match.tournament_id:
        return True
    if not match.tournament:
        return True
    return bool(match.tournament.affects_elo)


def bracket_state_for_tournament(tournament_id):
    matches = Match.query.filter(
        Match.tournament_id == tournament_id,
    ).order_by(
        Match.bracket_round.asc(),
        Match.bracket_slot.asc(),
        Match.id.asc(),
    ).all()
    grouped = {}
    for match in matches:
        rnd = int(match.bracket_round or 1)
        grouped.setdefault(rnd, [])
        data = match.to_dict()
        data['winner_user_id'] = winner_user_id(match)
        grouped[rnd].append(data)
    rounds = []
    for rnd in sorted(grouped):
        rounds.append({
            'round': rnd,
            'matches': grouped[rnd],
        })
    return {
        'rounds': rounds,
        'total_matches': len(matches),
    }


def seed_participants(participants):
    ordered = sorted(
        participants,
        key=lambda row: (row.checked_in_at is None, row.created_at or utcnow_naive(), row.user_id),
    )
    for index, participant in enumerate(ordered, start=1):
        participant.seed = index
    return ordered


def create_initial_single_elim_matches(tournament, participant_rows):
    """Create round 1 matches using seeded participants."""
    seeded = seed_participants(participant_rows)
    participant_ids = [row.user_id for row in seeded]
    bracket_size = len(participant_ids)
    rounds = total_rounds_for_size(bracket_size)
    if rounds is None:
        raise ValueError('Bracket size must be a power of two')

    users = User.query.filter(User.id.in_(participant_ids)).all()
    users_by_id = {user.id: user for user in users}
    created_matches = []

    for slot, idx in enumerate(range(0, bracket_size, 2), start=1):
        user1_id = participant_ids[idx]
        user2_id = participant_ids[idx + 1]
        match = Match(
            court_id=tournament.court_id,
            tournament_id=tournament.id,
            bracket_round=1,
            bracket_slot=slot,
            match_type=tournament.match_type,
            status='in_progress',
        )
        db.session.add(match)
        db.session.flush()
        db.session.add(MatchPlayer(
            match_id=match.id,
            user_id=user1_id,
            team=1,
            elo_before=users_by_id[user1_id].elo_rating if user1_id in users_by_id else None,
        ))
        db.session.add(MatchPlayer(
            match_id=match.id,
            user_id=user2_id,
            team=2,
            elo_before=users_by_id[user2_id].elo_rating if user2_id in users_by_id else None,
        ))
        created_matches.append(match)

    tournament.bracket_size = bracket_size
    tournament.total_rounds = rounds
    return created_matches


def serialize_tournament(
    tournament,
    *,
    include_participants=True,
    include_results=True,
    include_bracket=True,
):
    data = tournament.to_dict(
        include_participants=include_participants,
        include_results=include_results,
    )
    if include_bracket:
        data['bracket'] = bracket_state_for_tournament(tournament.id)
    return data


def _points_for_placement(placement, wins):
    if placement == 1:
        return 100
    if placement == 2:
        return 70
    if placement == 3:
        return 50
    base = max(10, 30 - (placement * 2))
    return base + (max(0, wins) * 8)


def _rankings_from_completed_matches(tournament):
    matches = Match.query.filter(
        Match.tournament_id == tournament.id,
        Match.status == 'completed',
    ).all()
    wins = {}
    losses = {}
    max_round_played = {}
    for match in matches:
        winner_id = winner_user_id(match)
        loser_id = loser_user_id(match)
        rnd = int(match.bracket_round or 1)
        for uid in (winner_id, loser_id):
            if uid is None:
                continue
            max_round_played[uid] = max(max_round_played.get(uid, 0), rnd)
        if winner_id is not None:
            wins[winner_id] = wins.get(winner_id, 0) + 1
        if loser_id is not None:
            losses[loser_id] = losses.get(loser_id, 0) + 1
    return wins, losses, max_round_played


def finalize_tournament_results(tournament):
    if tournament.status in {'completed', 'cancelled'}:
        return

    final_match = Match.query.filter_by(
        tournament_id=tournament.id,
        bracket_round=tournament.total_rounds,
        bracket_slot=1,
        status='completed',
    ).first()
    if not final_match:
        return

    winner_id = winner_user_id(final_match)
    runner_up_id = loser_user_id(final_match)
    if not winner_id or not runner_up_id:
        return

    wins, losses, max_round_played = _rankings_from_completed_matches(tournament)
    participant_rows = TournamentParticipant.query.filter(
        TournamentParticipant.tournament_id == tournament.id,
        TournamentParticipant.participant_status.notin_(['no_show', 'declined', 'withdrawn']),
    ).all()
    if not participant_rows:
        return

    placement_by_user = {winner_id: 1, runner_up_id: 2}
    if tournament.total_rounds and tournament.total_rounds > 1:
        semi_round = tournament.total_rounds - 1
        semi_matches = Match.query.filter_by(
            tournament_id=tournament.id,
            bracket_round=semi_round,
            status='completed',
        ).all()
        for match in semi_matches:
            loser_id_value = loser_user_id(match)
            if loser_id_value and loser_id_value not in placement_by_user:
                placement_by_user[loser_id_value] = 3

    remaining = []
    for row in participant_rows:
        uid = row.user_id
        if uid in placement_by_user:
            continue
        remaining.append({
            'user_id': uid,
            'wins': wins.get(uid, 0),
            'losses': losses.get(uid, 0),
            'max_round_played': max_round_played.get(uid, 0),
        })
    remaining.sort(
        key=lambda item: (
            -item['wins'],
            -item['max_round_played'],
            item['losses'],
            item['user_id'],
        )
    )
    next_place = 4
    for item in remaining:
        placement_by_user[item['user_id']] = next_place
        next_place += 1

    TournamentResult.query.filter_by(tournament_id=tournament.id).delete(synchronize_session=False)
    for row in participant_rows:
        uid = row.user_id
        placement = placement_by_user.get(uid)
        if not placement:
            continue
        wins_count = wins.get(uid, 0)
        losses_count = losses.get(uid, 0)
        points = _points_for_placement(placement, wins_count)
        row.final_placement = placement
        row.wins = wins_count
        row.losses = losses_count
        row.points = points
        if placement == 1:
            row.participant_status = 'winner'
        elif row.participant_status not in {'no_show', 'declined', 'withdrawn'}:
            row.participant_status = 'eliminated'
        db.session.add(TournamentResult(
            tournament_id=tournament.id,
            user_id=uid,
            court_id=tournament.court_id,
            placement=placement,
            wins=wins_count,
            losses=losses_count,
            points=points,
        ))
        db.session.add(Notification(
            user_id=uid,
            notif_type='tournament_result',
            content=(
                f'Tournament "{tournament.name}" complete. '
                f'You finished #{placement}.'
            ),
            reference_id=tournament.id,
        ))

    tournament.status = 'completed'
    tournament.completed_at = utcnow_naive()


def advance_tournament_after_completed_match(match):
    """Advance bracket when both sibling matches complete; finalize after final."""
    if not match or not match.tournament_id:
        return
    tournament = match.tournament
    if not tournament or tournament.status != 'live':
        return
    if match.status != 'completed':
        return
    if not winner_user_id(match):
        return

    current_round = int(match.bracket_round or 1)
    current_slot = int(match.bracket_slot or 1)
    total_rounds = int(tournament.total_rounds or 0)
    if total_rounds <= 0:
        return

    if current_round >= total_rounds:
        finalize_tournament_results(tournament)
        return

    sibling_slot = current_slot + 1 if (current_slot % 2 == 1) else current_slot - 1
    sibling = Match.query.filter_by(
        tournament_id=tournament.id,
        bracket_round=current_round,
        bracket_slot=sibling_slot,
    ).first()
    if not sibling or sibling.status != 'completed' or not winner_user_id(sibling):
        return

    if current_slot % 2 == 1:
        first_match, second_match = match, sibling
    else:
        first_match, second_match = sibling, match

    first_winner_id = winner_user_id(first_match)
    second_winner_id = winner_user_id(second_match)
    if not first_winner_id or not second_winner_id:
        return

    next_round = current_round + 1
    next_slot = (current_slot + 1) // 2
    existing_next = Match.query.filter_by(
        tournament_id=tournament.id,
        bracket_round=next_round,
        bracket_slot=next_slot,
    ).first()
    if existing_next:
        return

    users = User.query.filter(User.id.in_([first_winner_id, second_winner_id])).all()
    users_by_id = {user.id: user for user in users}
    next_match = Match(
        court_id=tournament.court_id,
        tournament_id=tournament.id,
        bracket_round=next_round,
        bracket_slot=next_slot,
        match_type=tournament.match_type,
        status='in_progress',
    )
    db.session.add(next_match)
    db.session.flush()
    db.session.add(MatchPlayer(
        match_id=next_match.id,
        user_id=first_winner_id,
        team=1,
        elo_before=users_by_id[first_winner_id].elo_rating if first_winner_id in users_by_id else None,
    ))
    db.session.add(MatchPlayer(
        match_id=next_match.id,
        user_id=second_winner_id,
        team=2,
        elo_before=users_by_id[second_winner_id].elo_rating if second_winner_id in users_by_id else None,
    ))
    db.session.add(Notification(
        user_id=first_winner_id,
        notif_type='tournament_match_ready',
        content=f'Your next tournament match in "{tournament.name}" is ready.',
        reference_id=next_match.id,
    ))
    db.session.add(Notification(
        user_id=second_winner_id,
        notif_type='tournament_match_ready',
        content=f'Your next tournament match in "{tournament.name}" is ready.',
        reference_id=next_match.id,
    ))
