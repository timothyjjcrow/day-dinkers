import { Capacitor } from '@capacitor/core'

const API_BASE_URL_KEY = 'thirdshot_api_base_url'
const PUBLIC_APP_URL_KEY = 'thirdshot_public_app_url'

function trimTrailingSlash(value: string) {
  return value.replace(/\/+$/, '')
}

function isAbsoluteUrl(value: string) {
  return /^https?:\/\//i.test(value)
}

export function isNativePlatform() {
  return Capacitor.isNativePlatform()
}

function readOverride(key: string) {
  return String(window.localStorage.getItem(key) || '').trim()
}

function writeOverride(key: string, value: string) {
  const trimmed = value.trim()
  if (!trimmed) {
    window.localStorage.removeItem(key)
    return
  }
  window.localStorage.setItem(key, trimTrailingSlash(trimmed))
}

export function getConfiguredApiBaseUrl() {
  const override = readOverride(API_BASE_URL_KEY)
  const envBase = String(import.meta.env.VITE_API_BASE_URL || '').trim()
  const raw = override || envBase

  if (raw) {
    return trimTrailingSlash(raw)
  }

  if (!isNativePlatform()) {
    return trimTrailingSlash(window.location.origin)
  }
  return ''
}

export function buildApiUrl(path: string) {
  if (isAbsoluteUrl(path)) {
    return path
  }

  const baseUrl = getConfiguredApiBaseUrl()
  if (!baseUrl) {
    return path
  }
  return new URL(path, `${baseUrl}/`).toString()
}

export function getSocketServerUrl() {
  const baseUrl = getConfiguredApiBaseUrl()
  if (baseUrl) {
    return baseUrl
  }
  return undefined
}

export function setConfiguredApiBaseUrl(value: string) {
  writeOverride(API_BASE_URL_KEY, value)
}

export function getPublicAppUrl() {
  const override = readOverride(PUBLIC_APP_URL_KEY)
  const envUrl = String(import.meta.env.VITE_PUBLIC_APP_URL || '').trim()
  const configuredApiBaseUrl = getConfiguredApiBaseUrl()
  const raw = override || envUrl || configuredApiBaseUrl

  if (raw) {
    return trimTrailingSlash(raw)
  }

  if (!isNativePlatform()) {
    const origin = String(window.location.origin || '').trim()
    if (isAbsoluteUrl(origin)) {
      return trimTrailingSlash(origin)
    }
  }
  return ''
}

export function buildPublicAppUrl(path: string) {
  const publicBaseUrl = getPublicAppUrl()
  if (!publicBaseUrl) {
    return ''
  }
  return new URL(path, `${publicBaseUrl}/`).toString()
}

export function setPublicAppUrl(value: string) {
  writeOverride(PUBLIC_APP_URL_KEY, value)
}
