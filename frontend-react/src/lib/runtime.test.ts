import { describe, expect, it } from 'vitest'

import {
  buildApiUrl,
  buildPublicAppUrl,
  getConfiguredApiBaseUrl,
  getPublicAppUrl,
  setConfiguredApiBaseUrl,
  setPublicAppUrl,
} from './runtime'

describe('runtime url helpers', () => {
  it('uses the current web origin when no override is set', () => {
    window.localStorage.removeItem('thirdshot_api_base_url')

    expect(getConfiguredApiBaseUrl()).toBe(window.location.origin)
    expect(buildApiUrl('/api/app/bootstrap')).toBe(`${window.location.origin}/api/app/bootstrap`)
  })

  it('prefers a runtime api override when present', () => {
    setConfiguredApiBaseUrl('https://api.thirdshot.test/')

    expect(getConfiguredApiBaseUrl()).toBe('https://api.thirdshot.test')
    expect(buildApiUrl('/api/courts')).toBe('https://api.thirdshot.test/api/courts')

    window.localStorage.removeItem('thirdshot_api_base_url')
  })

  it('builds a public share url from the current origin when available', () => {
    expect(buildPublicAppUrl('/courts/42')).toBe(`${window.location.origin}/courts/42`)
  })

  it('uses the configured api base when no public app url is set', () => {
    setConfiguredApiBaseUrl('https://api.thirdshot.test/')
    window.localStorage.removeItem('thirdshot_public_app_url')

    expect(getPublicAppUrl()).toBe('https://api.thirdshot.test')
    expect(buildPublicAppUrl('/courts/42')).toBe('https://api.thirdshot.test/courts/42')

    window.localStorage.removeItem('thirdshot_api_base_url')
  })

  it('prefers a runtime public url override when present', () => {
    setConfiguredApiBaseUrl('https://api.thirdshot.test/')
    setPublicAppUrl('https://play.thirdshot.test/')

    expect(getPublicAppUrl()).toBe('https://play.thirdshot.test')
    expect(buildPublicAppUrl('/courts/42')).toBe('https://play.thirdshot.test/courts/42')

    window.localStorage.removeItem('thirdshot_api_base_url')
    window.localStorage.removeItem('thirdshot_public_app_url')
  })
})
