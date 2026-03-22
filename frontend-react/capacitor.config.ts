import type { CapacitorConfig } from '@capacitor/cli'

const liveReloadUrl = String(process.env.CAP_SERVER_URL || '').trim()

const config: CapacitorConfig = {
  appId: 'com.thirdshot.mobile',
  appName: 'Third Shot',
  webDir: 'dist',
  plugins: {
    Keyboard: {
      resize: 'body',
      style: 'light',
      resizeOnFullScreen: true,
    },
  },
  server: liveReloadUrl
    ? {
        url: liveReloadUrl,
        cleartext: liveReloadUrl.startsWith('http://'),
      }
    : undefined,
}

export default config
