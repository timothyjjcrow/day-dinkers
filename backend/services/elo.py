"""
ELO Rating Engine for Pickleball — fair, team-aware rating system.

Key design decisions based on research:
- Start: 1200 ELO (standard chess default)
- K-factor: Adaptive (40 for new players, 32 for developing, 24 for established)
  New players' ratings move faster so they settle into the right bracket quickly.
- Doubles: Uses team average ELO for expected score calculation.
  Each player gains/loses individually based on team performance.
- Score margin: A modifier rewards decisive wins slightly more and prevents
  ELO inflation from big wins against weak opponents.
- Formula: E = 1 / (1 + 10^((opp_avg - team_avg) / 400))
           ΔR = K * margin_mult * (actual - expected)
"""
import math

DEFAULT_ELO = 1200.0


def get_k_factor(games_played):
    """Adaptive K-factor: higher for new players, lower for veterans."""
    if games_played < 10:
        return 40  # Provisional — fast adjustment
    if games_played < 30:
        return 32  # Developing — moderate adjustment
    return 24      # Established — slower, more stable


def expected_score(team_elo, opponent_elo):
    """Calculate the expected score (win probability) for a team.

    Uses the standard ELO expected score formula:
    E = 1 / (1 + 10^((opponent - team) / 400))
    """
    return 1.0 / (1.0 + math.pow(10, (opponent_elo - team_elo) / 400.0))


def score_margin_multiplier(winner_score, loser_score, elo_diff):
    """Score margin multiplier to reward convincing wins fairly.

    Uses a logarithmic formula that:
    - Gives slightly more ELO for blowouts
    - Autocorrects so beating a much weaker team by a lot doesn't
      give disproportionate ELO (prevents inflation)

    Args:
        winner_score: Points scored by the winning team.
        loser_score: Points scored by the losing team.
        elo_diff: Winner's team avg ELO minus loser's team avg ELO.
    """
    point_diff = max(winner_score - loser_score, 1)
    # Log-based margin: bigger diff = slightly more reward, but diminishing
    margin = math.log10(point_diff + 1)
    # Autocorrect factor: if a strong team beats a weak team big,
    # reduce the multiplier (prevents inflation)
    autocorrect = 2.2 / (abs(elo_diff) * 0.001 + 2.2)
    return max(0.5, min(margin * autocorrect + 0.5, 1.5))


def calculate_elo_changes(team1_players, team2_players,
                          team1_score, team2_score):
    """Calculate ELO changes for all players in a match.

    Args:
        team1_players: List of dicts with 'elo_rating' and 'games_played'.
        team2_players: List of dicts with 'elo_rating' and 'games_played'.
        team1_score: Final score for team 1.
        team2_score: Final score for team 2.

    Returns:
        (team1_changes, team2_changes) — lists of floats, one per player.
    """
    # Team average ELOs
    team1_avg = sum(p['elo_rating'] for p in team1_players) / len(team1_players)
    team2_avg = sum(p['elo_rating'] for p in team2_players) / len(team2_players)

    # Determine winner
    team1_won = team1_score > team2_score
    elo_diff = team1_avg - team2_avg if team1_won else team2_avg - team1_avg
    winner_score = max(team1_score, team2_score)
    loser_score = min(team1_score, team2_score)

    # Score margin multiplier
    margin_mult = score_margin_multiplier(winner_score, loser_score, elo_diff)

    # Team 1 expected score
    team1_expected = expected_score(team1_avg, team2_avg)
    team2_expected = 1.0 - team1_expected

    # Actual outcomes
    team1_actual = 1.0 if team1_won else 0.0
    team2_actual = 1.0 - team1_actual

    # Individual player changes
    team1_changes = []
    for p in team1_players:
        k = get_k_factor(p['games_played'])
        change = k * margin_mult * (team1_actual - team1_expected)
        team1_changes.append(round(change, 1))

    team2_changes = []
    for p in team2_players:
        k = get_k_factor(p['games_played'])
        change = k * margin_mult * (team2_actual - team2_expected)
        team2_changes.append(round(change, 1))

    return team1_changes, team2_changes
