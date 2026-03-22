const BANNER_EXPANDED_KEY = 'thirdshot_banner_expanded'

export function loadBannerExpanded() {
  return window.localStorage.getItem(BANNER_EXPANDED_KEY) === '1'
}

export function storeBannerExpanded(value: boolean) {
  window.localStorage.setItem(BANNER_EXPANDED_KEY, value ? '1' : '0')
}
