import { describe, expect, it } from 'vitest'

import { loadBannerExpanded, storeBannerExpanded } from './preferences'

describe('banner preferences', () => {
  it('persists expanded state in local storage', () => {
    window.localStorage.clear()

    expect(loadBannerExpanded()).toBe(false)

    storeBannerExpanded(true)
    expect(loadBannerExpanded()).toBe(true)

    storeBannerExpanded(false)
    expect(loadBannerExpanded()).toBe(false)
  })
})
