import { buildApiUrl } from './runtime'

const TOKEN_KEY = 'thirdshot_token'
const USER_KEY = 'thirdshot_user'
const CSRF_KEY = 'thirdshot_csrf'
const CSRF_FOR_KEY = 'thirdshot_csrf_for'

type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

function isMutating(method: HttpMethod) {
  return ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)
}

export function getStoredToken() {
  return window.localStorage.getItem(TOKEN_KEY)
}

export function getStoredUser() {
  const raw = window.localStorage.getItem(USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
}

export function setSession(token: string, user: unknown) {
  window.localStorage.setItem(TOKEN_KEY, token)
  window.localStorage.setItem(USER_KEY, JSON.stringify(user))
}

export function clearSession() {
  window.localStorage.removeItem(TOKEN_KEY)
  window.localStorage.removeItem(USER_KEY)
  window.localStorage.removeItem(CSRF_KEY)
  window.localStorage.removeItem(CSRF_FOR_KEY)
}

async function ensureCsrfToken(token: string) {
  const cachedToken = window.localStorage.getItem(CSRF_KEY)
  const cachedFor = window.localStorage.getItem(CSRF_FOR_KEY)
  if (cachedToken && cachedFor === token) {
    return cachedToken
  }

  const csrfUrl = buildApiUrl('/api/auth/csrf')

  const response = await fetch(csrfUrl, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })
  if (!response.ok) {
    window.localStorage.removeItem(CSRF_KEY)
    window.localStorage.removeItem(CSRF_FOR_KEY)
    return null
  }

  const data = (await response.json()) as { csrf_token?: string }
  if (!data.csrf_token) {
    return null
  }
  window.localStorage.setItem(CSRF_KEY, data.csrf_token)
  window.localStorage.setItem(CSRF_FOR_KEY, token)
  return data.csrf_token
}

async function request<T>(method: HttpMethod, url: string, body?: unknown): Promise<T> {
  const requestUrl = buildApiUrl(url)
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }
  const token = getStoredToken()
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }
  if (token && isMutating(method)) {
    const csrf = await ensureCsrfToken(token)
    if (csrf) {
      headers['X-CSRF-Token'] = csrf
    }
  }

  let response = await fetch(requestUrl, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })

  let payload: unknown = {}
  const contentType = response.headers.get('content-type') || ''
  if (contentType.includes('application/json')) {
    payload = await response.json()
  } else if (response.status !== 204) {
    payload = { error: await response.text() }
  }

  if (
    response.status === 403 &&
    typeof payload === 'object' &&
    payload !== null &&
    'error' in payload &&
    (payload as { error?: string }).error === 'Invalid CSRF token' &&
    token &&
    isMutating(method)
  ) {
    window.localStorage.removeItem(CSRF_KEY)
    window.localStorage.removeItem(CSRF_FOR_KEY)
    const freshCsrf = await ensureCsrfToken(token)
    if (freshCsrf) {
      response = await fetch(requestUrl, {
        method,
        headers: {
          ...headers,
          'X-CSRF-Token': freshCsrf,
        },
        body: body ? JSON.stringify(body) : undefined,
      })
      payload = await response.json()
    }
  }

  if (response.status === 401) {
    clearSession()
  }

  if (!response.ok) {
    const errorMessage =
      typeof payload === 'object' && payload && 'error' in payload
        ? String((payload as { error?: string }).error || 'Request failed')
        : 'Request failed'
    const error = new Error(errorMessage) as Error & { status?: number; payload?: unknown }
    error.status = response.status
    error.payload = payload
    throw error
  }

  return payload as T
}

export const api = {
  get: <T>(url: string) => request<T>('GET', url),
  post: <T>(url: string, body?: unknown) => request<T>('POST', url, body),
  put: <T>(url: string, body?: unknown) => request<T>('PUT', url, body),
  delete: <T>(url: string, body?: unknown) => request<T>('DELETE', url, body),
}
