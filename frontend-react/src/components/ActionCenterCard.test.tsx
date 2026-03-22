import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ActionCenterCard } from './ActionCenterCard'
import type { ActionCenterData } from '../types'

describe('ActionCenterCard', () => {
  it('sends the selected queue type with one tap join queue actions', async () => {
    const user = userEvent.setup()
    const onAction = vi.fn()
    const actionCenter: ActionCenterData = {
      type: 'join_queue',
      primary_action: {
        kind: 'join_queue',
        label: 'Join Queue',
        default_match_type: 'doubles',
      },
    }

    render(<ActionCenterCard actionCenter={actionCenter} queueCount={4} onAction={onAction} />)

    await user.click(screen.getByRole('button', { name: 'Singles' }))
    await user.click(screen.getByRole('button', { name: 'Join Queue' }))

    expect(onAction).toHaveBeenCalledWith(
      actionCenter.primary_action,
      expect.objectContaining({ matchType: 'singles' }),
    )
  })

  it('fires a one-tap quick challenge for the chosen checked-in player', async () => {
    const user = userEvent.setup()
    const onAction = vi.fn()
    const actionCenter: ActionCenterData = {
      type: 'quick_challenge',
      primary_action: {
        kind: 'challenge_player',
        label: 'Challenge',
      },
      challengeable_players: [
        {
          id: 8,
          username: 'jules',
          name: 'Jules',
          is_friend: true,
          looking_for_game: true,
        },
      ],
    }

    render(<ActionCenterCard actionCenter={actionCenter} queueCount={2} onAction={onAction} />)

    await user.click(screen.getByRole('button', { name: /Jules/i }))

    expect(onAction).toHaveBeenCalledWith(
      expect.objectContaining({
        kind: 'challenge_player',
        target_user_id: 8,
        label: 'Challenge Jules',
      }),
      {},
    )
  })
})
