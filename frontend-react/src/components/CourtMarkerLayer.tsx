import { useEffect, useRef } from 'react'
import L, { point, type DivIcon, type Marker } from 'leaflet'
import 'leaflet.markercluster'
import { useMap } from 'react-leaflet'

import type { BannerItem, CourtSummary, PresenceStatus } from '../types'

type MapCourtSummary = CourtSummary & {
  scheduled_count: number
}

interface CourtMarkerLayerProps {
  courts: MapCourtSummary[]
  markerIcons: Map<number, DivIcon>
  selectedCourtId: number | null
  nextScheduleByCourt: Map<number, BannerItem>
  currentPresence: PresenceStatus | null
  focusPaddingTop?: number
  onSelectCourt: (court: CourtSummary) => void
  onCloseCourt: (courtId: number) => void
  onOpenCourtDetails: (courtId: number) => void
  onDirections: (court: CourtSummary) => void
}

function escapeHtml(value: string | null | undefined) {
  return String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

function formatCourtAddress(court: CourtSummary) {
  return court.address || `${court.city || ''}${court.state ? `, ${court.state}` : ''}`.trim()
}

function formatNextCourtTime(isoString: string | null | undefined) {
  if (!isoString) return ''
  return new Date(isoString).toLocaleString([], {
    weekday: 'short',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function formatDistanceMiles(distance: number | null | undefined) {
  if (typeof distance !== 'number' || Number.isNaN(distance)) return null
  return `${distance.toFixed(1)} mi`
}

function buildClusterIcon(cluster: { getChildCount: () => number }) {
  const count = cluster.getChildCount()
  let size = 'small'
  if (count > 30) {
    size = 'large'
  } else if (count > 10) {
    size = 'medium'
  }

  return L.divIcon({
    html: `<div class="cluster-marker cluster-${size}"><span>${count}</span></div>`,
    className: 'court-cluster-icon',
    iconSize: point(42, 42),
  })
}

export function CourtMarkerLayer({
  courts,
  markerIcons,
  selectedCourtId,
  nextScheduleByCourt,
  currentPresence,
  focusPaddingTop = 24,
  onSelectCourt,
  onCloseCourt,
  onOpenCourtDetails,
  onDirections,
}: CourtMarkerLayerProps) {
  const map = useMap()
  const clusterGroupRef = useRef<L.MarkerClusterGroup | null>(null)
  const markerMapRef = useRef(new Map<number, Marker>())
  const activationMapRef = useRef(new Map<number, () => void>())

  useEffect(() => {
    const clusterGroup = L.markerClusterGroup({
      maxClusterRadius: 50,
      spiderfyOnMaxZoom: false,
      showCoverageOnHover: false,
      zoomToBoundsOnClick: false,
      disableClusteringAtZoom: 16,
      animate: true,
      chunkedLoading: true,
      removeOutsideVisibleBounds: true,
      spiderfyDistanceMultiplier: 2,
      iconCreateFunction: buildClusterIcon,
    })

    function handleClusterClick(event: {
      layer: L.MarkerCluster
      originalEvent?: Event
    }) {
      const cluster = event.layer
      const currentZoom = map.getZoom()
      const maxZoom = map.getMaxZoom()
      const bounds = cluster.getBounds()
      const targetZoom = map.getBoundsZoom(bounds, false, point(52, 52))
      const shouldSpiderfy = currentZoom >= 16 || targetZoom >= maxZoom - 1

      event.originalEvent?.preventDefault?.()
      event.originalEvent?.stopPropagation?.()

      if (shouldSpiderfy) {
        cluster.spiderfy()
        return
      }

      map.flyToBounds(bounds.pad(0.38), {
        animate: true,
        duration: currentZoom >= 13 ? 0.35 : 0.48,
        easeLinearity: 0.22,
        paddingTopLeft: [24, focusPaddingTop],
        paddingBottomRight: [24, 32],
        maxZoom: Math.min(Math.max(targetZoom + 1, currentZoom + 2), maxZoom - 1),
      })

      window.setTimeout(() => {
        attachMarkerDomListeners()
      }, 600)
    }

    clusterGroup.on('clusterclick', handleClusterClick)

    const mapContainer = map.getContainer()
    const domActivationHandlers = new Map<HTMLElement, EventListener>()
    const attachMarkerDomListeners = () => {
      mapContainer.querySelectorAll<HTMLElement>('.map-court-marker-icon').forEach((iconElement) => {
        if (domActivationHandlers.has(iconElement)) return

        const markerElement = iconElement.querySelector<HTMLElement>('.map-court-marker[data-court-id]')
        if (!markerElement) return

        const courtId = Number(markerElement.dataset.courtId)
        if (!Number.isInteger(courtId)) return

        const handleMarkerDomActivate: EventListener = (event) => {
          event.preventDefault()
          event.stopPropagation()
          activationMapRef.current.get(courtId)?.()
        }

        iconElement.dataset.tapBound = 'true'
        iconElement.addEventListener('click', handleMarkerDomActivate)
        iconElement.addEventListener('touchend', handleMarkerDomActivate, { passive: false })
        domActivationHandlers.set(iconElement, handleMarkerDomActivate)
      })
    }
    const observer = new MutationObserver(() => {
      attachMarkerDomListeners()
    })
    observer.observe(mapContainer, { childList: true, subtree: true })
    map.on('zoomend moveend', attachMarkerDomListeners)
    clusterGroup.on('animationend', attachMarkerDomListeners)
    clusterGroup.on('spiderfied', attachMarkerDomListeners)
    clusterGroup.on('unspiderfied', attachMarkerDomListeners)

    for (const court of courts) {
      const marker = L.marker([court.latitude, court.longitude], {
        icon: markerIcons.get(court.id),
        keyboard: false,
      })
      const openMarkerPopup = () => {
        const popup = marker.getPopup()
        if (!popup) return
        popup.setLatLng(marker.getLatLng())
        map.openPopup(popup)
      }
      const activateMarker = () => {
        onSelectCourt(court)
        if (typeof clusterGroup.zoomToShowLayer === 'function') {
          clusterGroup.zoomToShowLayer(marker, () => {
            openMarkerPopup()
          })
          return
        }
        openMarkerPopup()
      }
      activationMapRef.current.set(court.id, activateMarker)

      const nextItem = nextScheduleByCourt.get(court.id)
      const distance = formatDistanceMiles(court.distance)
      const popupRoot = document.createElement('div')
      popupRoot.className = 'court-popup-card'
      popupRoot.innerHTML = `
        ${court.photo_url ? `
          <div class="court-popup-thumb">
            <img src="${escapeHtml(court.photo_url)}" alt="${escapeHtml(court.name)}" />
          </div>
        ` : ''}
        <div class="popup-top-row">
          <div class="section-kicker popup-kicker">Court</div>
          <button
            type="button"
            class="popup-close-btn"
            aria-label="Close ${escapeHtml(court.name)}"
          >
            ×
          </button>
        </div>
        <div class="popup-header-block">
          <h3>${escapeHtml(court.name)}</h3>
          <p>${escapeHtml(formatCourtAddress(court))}</p>
        </div>
        <div class="popup-meta-row">
          ${distance ? `<span>${escapeHtml(distance)}</span>` : ''}
          <span>${Number(court.active_players || 0)} here now</span>
          ${Number(court.scheduled_count || 0) > 0 ? `<span>${Number(court.scheduled_count || 0)} scheduled</span>` : ''}
          <span>${Number(court.num_courts || 0)} courts</span>
          <span>${court.indoor ? 'Indoor' : 'Outdoor'}</span>
          ${currentPresence?.checked_in && currentPresence.court_id === court.id ? '<span>You are here</span>' : ''}
        </div>
        ${nextItem ? `
          <div class="popup-next-row">
            <strong>Next game</strong>
            <span>${escapeHtml(nextItem.title)} · ${escapeHtml(formatNextCourtTime(nextItem.start_time))}</span>
          </div>
        ` : ''}
        <div class="popup-actions">
          <button type="button" class="popup-action-btn" data-action="details">Court Details</button>
          <button type="button" class="popup-action-btn secondary" data-action="directions">Directions</button>
        </div>
      `

      popupRoot.querySelector<HTMLElement>('.popup-close-btn')?.addEventListener('click', (event) => {
        event.preventDefault()
        event.stopPropagation()
        marker.closePopup()
      })
      popupRoot.querySelector<HTMLElement>('[data-action="details"]')?.addEventListener('click', (event) => {
        event.preventDefault()
        event.stopPropagation()
        onOpenCourtDetails(court.id)
      })
      popupRoot.querySelector<HTMLElement>('[data-action="directions"]')?.addEventListener('click', (event) => {
        event.preventDefault()
        event.stopPropagation()
        onDirections(court)
      })

      marker.bindPopup(popupRoot, {
        className: 'court-popup-shell',
        closeButton: false,
        autoPan: true,
        keepInView: true,
      })

      marker.on('click', activateMarker)
      marker.on('mousedown', activateMarker)
      marker.on('touchstart', activateMarker)
      marker.on('popupopen', () => {
        onSelectCourt(court)
      })
      marker.on('popupclose', () => {
        onCloseCourt(court.id)
      })

      clusterGroup.addLayer(marker)
      markerMapRef.current.set(court.id, marker)
    }

    clusterGroup.addTo(map)
    clusterGroupRef.current = clusterGroup
    attachMarkerDomListeners()

    return () => {
      clusterGroup.off('clusterclick', handleClusterClick)
      observer.disconnect()
      map.off('zoomend moveend', attachMarkerDomListeners)
      clusterGroup.off('animationend', attachMarkerDomListeners)
      clusterGroup.off('spiderfied', attachMarkerDomListeners)
      clusterGroup.off('unspiderfied', attachMarkerDomListeners)
      domActivationHandlers.forEach((handler, iconElement) => {
        iconElement.removeEventListener('click', handler)
        iconElement.removeEventListener('touchend', handler)
        delete iconElement.dataset.tapBound
      })
      clusterGroup.removeFrom(map)
      markerMapRef.current.clear()
      activationMapRef.current.clear()
      clusterGroupRef.current = null
    }
  }, [
    courts,
    currentPresence?.checked_in,
    currentPresence?.court_id,
    map,
    markerIcons,
    nextScheduleByCourt,
    onCloseCourt,
    onDirections,
    focusPaddingTop,
    onOpenCourtDetails,
    onSelectCourt,
  ])

  useEffect(() => {
    if (!selectedCourtId) {
      markerMapRef.current.forEach((marker) => marker.closePopup())
      return
    }

    const clusterGroup = clusterGroupRef.current
    const marker = markerMapRef.current.get(selectedCourtId)
    if (!clusterGroup || !marker) return

    if (marker.isPopupOpen()) return

    const timerId = window.setTimeout(() => {
      if (typeof clusterGroup.zoomToShowLayer === 'function') {
        clusterGroup.zoomToShowLayer(marker, () => {
          const popup = marker.getPopup()
          if (!popup) return
          popup.setLatLng(marker.getLatLng())
          map.openPopup(popup)
        })
        return
      }
      const popup = marker.getPopup()
      if (!popup) return
      popup.setLatLng(marker.getLatLng())
      map.openPopup(popup)
    }, 0)

    return () => {
      window.clearTimeout(timerId)
    }
  }, [selectedCourtId])

  return null
}
