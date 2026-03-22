import { useState } from 'react'

import type { LeaderboardEntry, MatchPlayerSummary, MatchSummary } from '../types'

type CompetitiveTab = 'leaderboard' | 'recent'

interface CompetitiveHistoryPanelProps {
  leaderboard: LeaderboardEntry[]
  matches: MatchSummary[]
  currentUserId?: number | null
  defaultTab?: CompetitiveTab
  scopeLabel?: string
  compact?: boolean
}

function teamLabel(players: MatchPlayerSummary[] | undefined) {
  if (!players?.length) return 'TBD'
  return players
    .map((player) => player.user?.name || player.user?.username || 'Player')
    .join(' / ')
}

function userResult(match: MatchSummary, currentUserId: number | null | undefined) {
  if (!currentUserId || !match.winner_team) return null
  const me = match.players.find((player) => player.user_id === currentUserId)
  if (!me?.team) return null
  return me.team === match.winner_team ? 'Win' : 'Loss'
}

function formatCompletedAt(value: string | null | undefined) {
  if (!value) return 'Recently finished'
  return new Date(value).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

export function CompetitiveHistoryPanel({
  leaderboard,
  matches,
  currentUserId = null,
  defaultTab = 'leaderboard',
  scopeLabel = 'Local ladder',
  compact = false,
}: CompetitiveHistoryPanelProps) {
  const [activeTab, setActiveTab] = useState<CompetitiveTab>(defaultTab)
  const topThree = leaderboard.slice(0, 3)
  const listRows = leaderboard.slice(0, compact ? 5 : 8)
  const currentRow = leaderboard.find((entry) => entry.user_id === currentUserId) || null
  const pinnedCurrentRow = currentRow && !listRows.some((entry) => entry.user_id === currentRow.user_id)
    ? currentRow
    : null
  const visibleMatches = matches.slice(0, compact ? 3 : 6)
  const summaryPills = [
    currentRow ? `#${currentRow.rank}` : 'Unranked',
    leaderboard.length ? `${leaderboard.length} players` : null,
    matches.length ? `${matches.length} saved` : null,
  ].filter(Boolean)
  const leaderboardLabel = compact ? 'Ladder' : 'Leaderboard'

  return (
    <section className={`competitive-panel ${compact ? 'compact' : ''}`.trim()}>
      <div className="competitive-panel-head">
        <div className="competitive-panel-copy">
          {compact ? null : <div className="section-kicker">Competitive</div>}
          <strong>{scopeLabel}</strong>
          {compact ? (
            <div className="competitive-inline-pills">
              {summaryPills.map((pill) => (
                <span key={pill} className="queue-pill">
                  {pill}
                </span>
              ))}
            </div>
          ) : null}
        </div>
        <div className="tab-row compact-tabs">
          <button
            type="button"
            className={activeTab === 'leaderboard' ? 'active' : ''}
            onClick={() => setActiveTab('leaderboard')}
          >
            {leaderboardLabel}
          </button>
          <button
            type="button"
            className={activeTab === 'recent' ? 'active' : ''}
            onClick={() => setActiveTab('recent')}
          >
            Recent
          </button>
        </div>
      </div>

      {activeTab === 'leaderboard' ? (
        leaderboard.length ? (
          <div className="competitive-stack">
            <div className="leaderboard-podium">
              {topThree.map((entry) => (
                <article
                  key={entry.user_id}
                  className={`leaderboard-podium-card rank-${entry.rank} ${entry.user_id === currentUserId ? 'current' : ''}`}
                >
                  <span className="leaderboard-rank">#{entry.rank}</span>
                  <strong>{entry.name}</strong>
                  <span>ELO {entry.elo_rating}</span>
                  <em>{entry.win_rate}% win</em>
                </article>
              ))}
            </div>

            <div className="leaderboard-list">
              {listRows.map((entry) => (
                <article
                  key={entry.user_id}
                  className={`leaderboard-row-card ${entry.user_id === currentUserId ? 'current' : ''}`}
                >
                  <div className="leaderboard-row-main">
                    <strong>#{entry.rank} {entry.name}</strong>
                    <span>@{entry.username}</span>
                  </div>
                  <div className="leaderboard-row-stats">
                    <strong>{entry.elo_rating}</strong>
                    <span>{entry.wins}-{entry.losses}</span>
                  </div>
                </article>
              ))}
              {pinnedCurrentRow ? (
                <article className="leaderboard-row-card current pinned">
                  <div className="leaderboard-row-main">
                    <strong>#{pinnedCurrentRow.rank} {pinnedCurrentRow.name}</strong>
                    <span>Your current line</span>
                  </div>
                  <div className="leaderboard-row-stats">
                    <strong>{pinnedCurrentRow.elo_rating}</strong>
                    <span>{pinnedCurrentRow.wins}-{pinnedCurrentRow.losses}</span>
                  </div>
                </article>
              ) : null}
            </div>
          </div>
        ) : (
          <div className="empty-card">Complete ranked games to light up this leaderboard.</div>
        )
      ) : (
        visibleMatches.length ? (
          <div className="match-history-list">
            {visibleMatches.map((match) => {
              const result = userResult(match, currentUserId)
              const scoreline = match.team1_score !== null && match.team1_score !== undefined
                && match.team2_score !== null && match.team2_score !== undefined
                ? `${match.team1_score} - ${match.team2_score}`
                : 'Score pending'

              return (
                <article key={match.id} className="match-history-card">
                  <div className="match-history-top">
                    <div>
                      <strong>{teamLabel(match.team1)}</strong>
                      <span>vs {teamLabel(match.team2)}</span>
                    </div>
                    <div className="match-history-score">
                      {result ? <em className={`match-result-pill ${result.toLowerCase()}`}>{result}</em> : null}
                      <strong>{scoreline}</strong>
                    </div>
                  </div>
                  <div className="match-history-meta">
                    <span>{match.match_type}</span>
                    <span>{match.court?.name || 'Court match'}</span>
                    <span>{formatCompletedAt(match.completed_at)}</span>
                  </div>
                </article>
              )
            })}
          </div>
        ) : (
          <div className="empty-card">No completed ranked matches yet.</div>
        )
      )}
    </section>
  )
}
