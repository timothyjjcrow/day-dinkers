import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { CompetitiveHistoryPanel } from './CompetitiveHistoryPanel'
import type { LeaderboardEntry, MatchSummary } from '../types'

const leaderboard: LeaderboardEntry[] = [
  { rank: 1, user_id: 3, name: 'Alex Ace', username: 'alex', elo_rating: 1260, wins: 8, losses: 2, games_played: 10, win_rate: 80 },
  { rank: 2, user_id: 7, name: 'You', username: 'you', elo_rating: 1235, wins: 6, losses: 3, games_played: 9, win_rate: 67 },
  { rank: 3, user_id: 9, name: 'Jamie Spin', username: 'jamie', elo_rating: 1218, wins: 5, losses: 4, games_played: 9, win_rate: 56 },
]

const matches: MatchSummary[] = [
  {
    id: 21,
    court_id: 1,
    match_type: 'singles',
    status: 'completed',
    team1_score: 11,
    team2_score: 8,
    winner_team: 1,
    completed_at: '2026-03-21T12:00:00',
    players: [
      { user_id: 7, team: 1, user: { id: 7, username: 'you', name: 'You' } },
      { user_id: 9, team: 2, user: { id: 9, username: 'jamie', name: 'Jamie Spin' } },
    ],
    team1: [{ user_id: 7, team: 1, user: { id: 7, username: 'you', name: 'You' } }],
    team2: [{ user_id: 9, team: 2, user: { id: 9, username: 'jamie', name: 'Jamie Spin' } }],
  },
]

describe('CompetitiveHistoryPanel', () => {
  it('toggles between leaderboard and recent match views', async () => {
    const user = userEvent.setup()

    render(
      <CompetitiveHistoryPanel
        leaderboard={leaderboard}
        matches={matches}
        currentUserId={7}
        scopeLabel="County ladder"
      />,
    )

    expect(screen.getByText(/County ladder/i)).toBeInTheDocument()
    expect(screen.getByText(/#2 You/i)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Recent/i }))

    expect(screen.getByText(/11 - 8/i)).toBeInTheDocument()
    expect(screen.getByText(/Win/i)).toBeInTheDocument()
  })
})
