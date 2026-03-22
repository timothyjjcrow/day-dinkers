interface GoogleCredentialResponse {
  credential?: string
}

interface GoogleIdConfiguration {
  client_id: string
  callback: (response: GoogleCredentialResponse) => void
}

interface GoogleButtonConfiguration {
  theme?: 'outline' | 'filled_blue' | 'filled_black'
  size?: 'large' | 'medium' | 'small'
  text?: 'continue_with' | 'signin_with' | 'signup_with'
  shape?: 'pill' | 'rectangular' | 'square' | 'circle'
  width?: number
}

interface GoogleIdentityApi {
  initialize(config: GoogleIdConfiguration): void
  renderButton(parent: HTMLElement, options: GoogleButtonConfiguration): void
}

interface GoogleNamespace {
  accounts?: {
    id?: GoogleIdentityApi
  }
}

declare global {
  interface Window {
    google?: GoogleNamespace
  }
}

const GOOGLE_SCRIPT_ID = 'thirdshot-google-identity'
const GOOGLE_SCRIPT_URL = 'https://accounts.google.com/gsi/client'

let googleScriptPromise: Promise<GoogleNamespace> | null = null

export interface GoogleAuthConfig {
  enabled: boolean
  client_id?: string | null
}

export function loadGoogleIdentityScript() {
  if (window.google?.accounts?.id) {
    return Promise.resolve(window.google)
  }

  if (googleScriptPromise) {
    return googleScriptPromise
  }

  googleScriptPromise = new Promise<GoogleNamespace>((resolve, reject) => {
    const existingScript = document.getElementById(GOOGLE_SCRIPT_ID) as HTMLScriptElement | null
    if (existingScript) {
      existingScript.addEventListener('load', () => resolve(window.google || {}), { once: true })
      existingScript.addEventListener('error', () => reject(new Error('Unable to load Google Sign-In.')), {
        once: true,
      })
      return
    }

    const script = document.createElement('script')
    script.id = GOOGLE_SCRIPT_ID
    script.src = GOOGLE_SCRIPT_URL
    script.async = true
    script.defer = true
    script.onload = () => resolve(window.google || {})
    script.onerror = () => reject(new Error('Unable to load Google Sign-In.'))
    document.head.appendChild(script)
  })

  return googleScriptPromise
}
