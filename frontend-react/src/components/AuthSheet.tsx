import { useEffect, useRef, useState } from 'react'

import { api, setSession } from '../lib/api'
import { loadGoogleIdentityScript } from '../lib/googleIdentity'
import { BottomSheet } from './BottomSheet'

interface AuthSheetProps {
  open: boolean
  onClose: () => void
  onSuccess: () => Promise<void> | void
}

export function AuthSheet({ open, onClose, onSuccess }: AuthSheetProps) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [googleClientId, setGoogleClientId] = useState('')
  const [googleReady, setGoogleReady] = useState(false)
  const [googleUnavailable, setGoogleUnavailable] = useState('')
  const [form, setForm] = useState({
    email: '',
    password: '',
    username: '',
    name: '',
  })
  const googleButtonRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    setError('')
  }, [open])

  useEffect(() => {
    if (!open || mode !== 'login') return undefined

    let active = true
    setGoogleReady(false)
    setGoogleUnavailable('')

    void api
      .get<{ enabled: boolean; client_id?: string | null }>('/api/auth/google/config')
      .then((config) => {
        if (!active) return
        const clientId = String(config?.client_id || '').trim()
        if (!config?.enabled || !clientId) {
          setGoogleClientId('')
          setGoogleUnavailable('Google sign-in is not configured on this server yet.')
          return
        }
        setGoogleClientId(clientId)
      })
      .catch(() => {
        if (!active) return
        setGoogleClientId('')
        setGoogleUnavailable('Unable to load Google sign-in right now.')
      })

    return () => {
      active = false
    }
  }, [mode, open])

  useEffect(() => {
    if (!open || mode !== 'login' || !googleClientId || !googleButtonRef.current) return undefined

    let active = true
    const container = googleButtonRef.current
    container.innerHTML = ''

    void loadGoogleIdentityScript()
      .then((google) => {
        if (!active || !container) return
        const googleIdentity = google.accounts?.id
        if (!googleIdentity) {
          setGoogleReady(false)
          setGoogleUnavailable('Google Sign-In is still loading. Please try again.')
          return
        }

        googleIdentity.initialize({
          client_id: googleClientId,
          callback: (response) => {
            if (!active) return
            void handleGoogleCredentialResponse(response)
          },
        })
        container.innerHTML = ''
        googleIdentity.renderButton(container, {
          theme: 'outline',
          size: 'large',
          text: 'continue_with',
          shape: 'pill',
          width: 320,
        })
        setGoogleReady(true)
        setGoogleUnavailable('')
      })
      .catch((scriptError) => {
        if (!active) return
        setGoogleReady(false)
        setGoogleUnavailable(
          scriptError instanceof Error ? scriptError.message : 'Unable to initialize Google Sign-In.',
        )
      })

    return () => {
      active = false
      container.innerHTML = ''
    }
  }, [googleClientId, mode, open])

  async function handleGoogleCredentialResponse(response: { credential?: string }) {
    if (!response?.credential) {
      setError('Google did not return a valid sign-in token.')
      return
    }

    setPending(true)
    setError('')

    try {
      const authResponse = await api.post<{ token: string; user: unknown }>('/api/auth/google', {
        id_token: response.credential,
      })
      setSession(authResponse.token, authResponse.user)
      await onSuccess()
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : 'Google sign-in failed')
    } finally {
      setPending(false)
    }
  }

  async function handleSubmit() {
    setPending(true)
    setError('')

    try {
      const endpoint = mode === 'login' ? '/api/auth/login' : '/api/auth/register'
      const payload = mode === 'login'
        ? {
            email: form.email.trim(),
            password: form.password,
          }
        : {
            username: form.username.trim(),
            name: form.name.trim(),
            email: form.email.trim(),
            password: form.password,
          }

      const response = await api.post<{ token: string; user: unknown }>(endpoint, payload)
      setSession(response.token, response.user)
      await onSuccess()
      setForm({
        email: '',
        password: '',
        username: '',
        name: '',
      })
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : 'Authentication failed')
    } finally {
      setPending(false)
    }
  }

  return (
    <BottomSheet
      open={open}
      title="Sign In To Play"
      eyebrow="Guest friendly"
      subtitle="Browse first. Sign in when you're ready to check in, schedule, challenge, or chat."
      onClose={onClose}
      variant="action"
    >
      <div className="auth-sheet">
        <div className="auth-tabs">
          <button
            type="button"
            className={mode === 'login' ? 'active' : ''}
            onClick={() => setMode('login')}
          >
            Login
          </button>
          <button
            type="button"
            className={mode === 'register' ? 'active' : ''}
            onClick={() => setMode('register')}
          >
            Register
          </button>
        </div>

        {mode === 'login' ? (
          <div className="google-auth-section">
            {googleClientId ? (
              <>
                <div ref={googleButtonRef} className="google-button-host" />
                {!googleReady ? <p className="auth-note">Loading Google sign-in…</p> : null}
                <div className="google-divider">
                  <span>or use email</span>
                </div>
              </>
            ) : googleUnavailable ? (
              <p className="auth-note">{googleUnavailable}</p>
            ) : null}
          </div>
        ) : null}

        <label className="form-field">
          <span>Email</span>
          <input
            type="email"
            value={form.email}
            onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))}
            placeholder="you@example.com"
          />
        </label>

        {mode === 'register' ? (
          <>
            <label className="form-field">
              <span>Username</span>
              <input
                type="text"
                value={form.username}
                onChange={(event) => setForm((current) => ({ ...current, username: event.target.value }))}
                placeholder="Court handle"
              />
            </label>
            <label className="form-field">
              <span>Name</span>
              <input
                type="text"
                value={form.name}
                onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
                placeholder="Display name"
              />
            </label>
          </>
        ) : null}

        <label className="form-field">
          <span>Password</span>
          <input
            type="password"
            value={form.password}
            onChange={(event) => setForm((current) => ({ ...current, password: event.target.value }))}
            placeholder="Your password"
          />
        </label>

        <p className="auth-note">
          Use email or Google, then pick up right where you left off.
        </p>

        {error ? <div className="error-note">{error}</div> : null}

        <button type="button" className="primary-btn full-width" onClick={handleSubmit} disabled={pending}>
          {pending ? 'Working...' : mode === 'login' ? 'Continue To Ranked Play' : 'Create Account'}
        </button>
      </div>
    </BottomSheet>
  )
}
