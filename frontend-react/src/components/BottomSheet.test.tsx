import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { BottomSheet } from './BottomSheet'

describe('BottomSheet', () => {
  it('keeps the close control clickable for action-style sheets', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()

    render(
      <BottomSheet
        open
        title="Post Final Score"
        eyebrow="Ranked Match"
        subtitle="Save result to ladder and profiles"
        onClose={onClose}
        variant="action"
      >
        <div>Body</div>
      </BottomSheet>,
    )

    const dialog = screen.getByRole('dialog', { name: 'Post Final Score' })
    await user.click(within(dialog).getByRole('button', { name: 'Close' }))

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('keeps the close control clickable for default sheets', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()

    render(
      <BottomSheet open title="Filters" onClose={onClose}>
        <div>Body</div>
      </BottomSheet>,
    )

    const dialog = screen.getByRole('dialog', { name: 'Filters' })
    await user.click(within(dialog).getByRole('button', { name: 'Close' }))

    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
