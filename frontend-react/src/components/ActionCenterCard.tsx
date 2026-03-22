import { useState } from 'react'

import type { ActionButtonPayload, ActionCenterData, QueueReadyCohort, UserSummary } from '../types'

interface ActionCenterCardProps {
  actionCenter: ActionCenterData
  queueCount: number
  compact?: boolean
  embedded?: boolean
  minimal?: boolean
  onAction: (action: ActionButtonPayload, extra?: { matchType?: 'singles' | 'doubles'; cohort?: QueueReadyCohort }) => void
}

function titleForAction(actionCenter: ActionCenterData) {
  switch (actionCenter.type) {
    case 'confirm_score':
      return 'Confirm Score'
    case 'enter_score':
      return 'Enter Score'
    case 'respond_invite':
      return 'Respond To Invite'
    case 'start_ready_game':
      return 'Ready To Start'
    case 'start_next_queue_game':
      return 'Queue Ready'
    case 'check_in':
      return 'Check In'
    case 'quick_challenge':
      return 'Quick Challenge'
    case 'queue_status':
      return 'In Queue'
    case 'join_queue':
      return 'Join Ranked Queue'
    default:
      return 'Schedule Ranked'
  }
}

function subtitleForAction(actionCenter: ActionCenterData) {
  switch (actionCenter.type) {
    case 'confirm_score':
      return 'Confirm the posted result in one tap.'
    case 'enter_score':
      return 'Post the final score from a quick sheet.'
    case 'respond_invite':
      return 'Accept or decline right here.'
    case 'start_ready_game':
      return 'Everyone is set. Start now.'
    case 'start_next_queue_game':
      return 'The next queue group is ready.'
    case 'check_in':
      return 'Check in to unlock ranked play.'
    case 'quick_challenge':
      return 'Friends show first for the fastest matchup.'
    case 'queue_status':
      return 'Your spot is locked in.'
    case 'join_queue':
      return 'Pick singles or doubles and jump in.'
    default:
      return 'Set a future ranked game from this court.'
  }
}

function flowStepsForAction(actionCenter: ActionCenterData) {
  const current =
    actionCenter.type === 'check_in'
      ? 'checkin'
      : ['join_queue', 'queue_status', 'quick_challenge', 'respond_invite'].includes(actionCenter.type)
        ? 'matchup'
        : ['start_ready_game', 'start_next_queue_game'].includes(actionCenter.type)
          ? 'play'
          : ['enter_score', 'confirm_score'].includes(actionCenter.type)
            ? 'score'
            : 'schedule'

  return [
    { key: 'checkin', label: 'Check in', state: current === 'checkin' ? 'current' : 'done' },
    {
      key: 'matchup',
      label: actionCenter.type === 'quick_challenge' ? 'Challenge' : 'Match up',
      state: current === 'matchup' ? 'current' : ['play', 'score'].includes(current) ? 'done' : 'upcoming',
    },
    {
      key: 'play',
      label: 'Play',
      state: current === 'play' ? 'current' : current === 'score' ? 'done' : 'upcoming',
    },
    { key: 'score', label: 'Score', state: current === 'score' ? 'current' : 'upcoming' },
  ] as const
}

function contextNote(actionCenter: ActionCenterData) {
  if (actionCenter.type === 'respond_invite' && actionCenter.lobby) {
    const names = (actionCenter.lobby.players || [])
      .map((player) => player.user?.name || player.user?.username)
      .filter(Boolean)
      .join(' · ')
    return names ? `Invite with ${names}` : 'Invite ready to answer'
  }
  if (actionCenter.type === 'start_ready_game' && actionCenter.lobby) {
    return `${actionCenter.lobby.match_type} lobby is ready now`
  }
  if ((actionCenter.type === 'enter_score' || actionCenter.type === 'confirm_score') && actionCenter.match) {
    const scoreline = actionCenter.match.team1_score !== null && actionCenter.match.team1_score !== undefined
      && actionCenter.match.team2_score !== null && actionCenter.match.team2_score !== undefined
      ? `${actionCenter.match.team1_score} - ${actionCenter.match.team2_score}`
      : 'Score not posted yet'
    return `${actionCenter.match.match_type} match · ${scoreline}`
  }
  if (actionCenter.type === 'start_next_queue_game' && actionCenter.queue_ready_cohort) {
    return `${actionCenter.queue_ready_cohort.entries.length} players are grouped and ready`
  }
  if (actionCenter.type === 'queue_status' && actionCenter.queue_entry) {
    return `You are queued for ${actionCenter.queue_entry.match_type}`
  }
  return ''
}

function ChallengeChips({
  players,
  onChallenge,
}: {
  players: UserSummary[]
  onChallenge: (player: UserSummary) => void
}) {
  if (!players.length) return null
  return (
    <div className="chip-row">
      {players.slice(0, 4).map((player) => (
        <button
          key={player.id}
          type="button"
          className={`player-chip ${player.is_friend ? 'friend' : ''}`}
          onClick={() => onChallenge(player)}
        >
          <span className="player-chip-name">{player.name || player.username}</span>
          <span className="player-chip-meta">
            {player.is_friend ? 'Friend' : 'Player'}
            {player.looking_for_game ? ' · Ready' : ''}
          </span>
        </button>
      ))}
    </div>
  )
}

export function ActionCenterCard({
  actionCenter,
  queueCount,
  compact = false,
  embedded = false,
  minimal = false,
  onAction,
}: ActionCenterCardProps) {
  const initialType = actionCenter.primary_action.default_match_type || 'doubles'
  const [queueType, setQueueType] = useState<'singles' | 'doubles'>(
    initialType === 'singles' ? 'singles' : 'doubles',
  )
  const flowSteps = flowStepsForAction(actionCenter)
  const note = contextNote(actionCenter)

  return (
    <section className={`action-card ${compact ? 'compact' : ''} ${embedded ? 'embedded' : ''} ${minimal ? 'minimal' : ''}`}>
      {minimal ? null : <div className="section-kicker">Competitive</div>}
      <div className="action-card-header">
        <div>
          <h2>{titleForAction(actionCenter)}</h2>
          {minimal ? null : <p>{subtitleForAction(actionCenter)}</p>}
        </div>
        {minimal ? null : <span className="queue-pill">{queueCount} in queue</span>}
      </div>

      <div className="action-flow-strip" aria-label="Ranked play steps">
        {flowSteps.map((step) => (
          <span key={step.key} className={`action-flow-chip ${step.state}`}>
            {step.label}
          </span>
        ))}
      </div>

      {note ? <div className="inline-note compact action-context-note">{note}</div> : null}

      {actionCenter.type === 'join_queue' ? (
        <div className="segmented-control" role="group" aria-label="Queue match type">
          <button
            type="button"
            className={queueType === 'singles' ? 'active' : ''}
            onClick={() => setQueueType('singles')}
          >
            Singles
          </button>
          <button
            type="button"
            className={queueType === 'doubles' ? 'active' : ''}
            onClick={() => setQueueType('doubles')}
          >
            Doubles
          </button>
        </div>
      ) : null}

      {actionCenter.type === 'quick_challenge' ? (
        <ChallengeChips
          players={actionCenter.challengeable_players || []}
          onChallenge={(player) =>
            onAction(
              {
                ...actionCenter.primary_action,
                target_user_id: player.id,
                label: `Challenge ${player.name || player.username}`,
              },
              {},
            )
          }
        />
      ) : null}

      {actionCenter.type === 'queue_status' && actionCenter.queue_entry ? (
        <div className="inline-stat-grid">
          <div>
            <strong>{actionCenter.queue_entry.match_type}</strong>
            <span>Queue type</span>
          </div>
          <div>
            <strong>#{actionCenter.queue_entry.match_type_position || actionCenter.queue_entry.position || 1}</strong>
            <span>Queue spot</span>
          </div>
        </div>
      ) : null}

      {actionCenter.type === 'start_next_queue_game' && actionCenter.queue_ready_cohort ? (
        <div className="inline-note">
          Next {actionCenter.queue_ready_cohort.match_type} group:
          {' '}
          {actionCenter.queue_ready_cohort.entries
            .map((entry) => entry.user.name || entry.user.username)
            .join(' vs ')}
        </div>
      ) : null}

      <div className="action-row">
        <button
          type="button"
          className="primary-btn"
          onClick={() =>
            onAction(actionCenter.primary_action, {
              matchType: queueType,
              cohort: actionCenter.queue_ready_cohort,
            })
          }
        >
          {actionCenter.primary_action.label}
        </button>
        {actionCenter.secondary_action ? (
          <button
            type="button"
            className="secondary-btn"
            onClick={() => onAction(actionCenter.secondary_action!, { matchType: queueType })}
          >
            {actionCenter.secondary_action.label}
          </button>
        ) : null}
      </div>
    </section>
  )
}
