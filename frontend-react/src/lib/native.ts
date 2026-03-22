import { useEffect } from 'react'
import type { PluginListenerHandle } from '@capacitor/core'
import { Browser } from '@capacitor/browser'
import { Geolocation } from '@capacitor/geolocation'
import { Keyboard } from '@capacitor/keyboard'
import { Share } from '@capacitor/share'

import { isNativePlatform } from './runtime'

export async function getCurrentPosition() {
  if (isNativePlatform()) {
    const permissions = await Geolocation.requestPermissions()
    if (permissions.location === 'denied' || permissions.coarseLocation === 'denied') {
      throw new Error('Location permission is required to use Near Me.')
    }
    return Geolocation.getCurrentPosition({
      enableHighAccuracy: true,
      timeout: 10_000,
    })
  }

  if (!navigator.geolocation) {
    throw new Error('Geolocation is not available on this device.')
  }

  return new Promise<GeolocationPosition>((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(resolve, reject, {
      enableHighAccuracy: true,
      timeout: 10_000,
    })
  })
}

export async function shareLink(payload: { title?: string; text?: string; url?: string }) {
  if (isNativePlatform()) {
    return Share.share(payload)
  }

  if (navigator.share) {
    return navigator.share(payload)
  }

  if (navigator.clipboard && payload.url) {
    await navigator.clipboard.writeText(payload.url)
    return
  }

  window.alert(payload.url || payload.text || payload.title || 'Nothing to share')
}

export async function openExternalUrl(url: string) {
  if (isNativePlatform()) {
    await Browser.open({ url })
    return
  }

  window.open(url, '_blank', 'noopener,noreferrer')
}

export function useNativeKeyboardClass() {
  useEffect(() => {
    if (!isNativePlatform()) return undefined

    const handles: PluginListenerHandle[] = []

    const addKeyboardClass = () => document.body.classList.add('keyboard-open')
    const removeKeyboardClass = () => document.body.classList.remove('keyboard-open')

    let active = true
    Promise.all([
      Keyboard.addListener('keyboardWillShow', addKeyboardClass),
      Keyboard.addListener('keyboardWillHide', removeKeyboardClass),
    ]).then((listenerHandles) => {
      if (!active) {
        listenerHandles.forEach((handle) => {
          void handle.remove()
        })
        return
      }
      handles.push(...listenerHandles)
    })

    return () => {
      active = false
      removeKeyboardClass()
      handles.forEach((handle) => {
        void handle.remove()
      })
    }
  }, [])
}
