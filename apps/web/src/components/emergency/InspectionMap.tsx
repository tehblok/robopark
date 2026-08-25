import { useEffect, useRef } from 'react'
import L from 'leaflet'
import iconUrl from 'leaflet/dist/images/marker-icon.png'
import iconRetinaUrl from 'leaflet/dist/images/marker-icon-2x.png'
import shadowUrl from 'leaflet/dist/images/marker-shadow.png'
import 'leaflet/dist/leaflet.css'

const MARKER_MS = 2500
const FOLLOW_PAN_S = 2.4

type DefaultIconProto = { _getIconUrl?: unknown }

delete (L.Icon.Default.prototype as DefaultIconProto)._getIconUrl
L.Icon.Default.mergeOptions({ iconRetinaUrl, iconUrl, shadowUrl })

export function InspectionMap({
  lat,
  lon,
  follow,
  onUserPan,
  visible = true,
}: {
  lat: number | null
  lon: number | null
  follow: boolean
  onUserPan: () => void
  visible?: boolean
}) {
  const rootRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<L.Map | null>(null)
  const markerRef = useRef<L.Marker | null>(null)
  const skipPanRef = useRef(false)
  const onUserPanRef = useRef(onUserPan)
  const animRef = useRef<number | null>(null)

  useEffect(() => {
    onUserPanRef.current = onUserPan
  }, [onUserPan])

  useEffect(() => {
    if (!rootRef.current || mapRef.current) return
    const map = L.map(rootRef.current, { zoomControl: true }).setView([55.75, 37.62], 16)
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap',
    }).addTo(map)
    map.on('dragstart', () => onUserPanRef.current())
    map.on('zoomstart', () => {
      if (!skipPanRef.current) onUserPanRef.current()
    })
    mapRef.current = map
    const sizeTimer = window.setTimeout(() => map.invalidateSize(), 0)
    return () => {
      window.clearTimeout(sizeTimer)
      if (animRef.current != null) {
        window.cancelAnimationFrame(animRef.current)
        animRef.current = null
      }
      map.remove()
      mapRef.current = null
      markerRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!visible) return
    const map = mapRef.current
    if (!map) return
    const sizeTimer = window.setTimeout(() => map.invalidateSize(), 0)
    return () => window.clearTimeout(sizeTimer)
  }, [visible])

  useEffect(() => {
    const map = mapRef.current
    if (!map || lat == null || lon == null) return
    skipPanRef.current = true
    if (animRef.current != null) {
      window.cancelAnimationFrame(animRef.current)
      animRef.current = null
    }
    if (!markerRef.current) {
      markerRef.current = L.marker([lat, lon]).addTo(map)
      map.setView([lat, lon], 17, { animate: false })
    } else {
      const marker = markerRef.current
      const from = marker.getLatLng()
      const start = performance.now()
      const tick = (now: number) => {
        const t = Math.min(1, (now - start) / MARKER_MS)
        marker.setLatLng([
          from.lat + (lat - from.lat) * t,
          from.lng + (lon - from.lng) * t,
        ])
        if (t < 1) {
          animRef.current = window.requestAnimationFrame(tick)
        } else {
          animRef.current = null
        }
      }
      animRef.current = window.requestAnimationFrame(tick)
      if (follow) {
        map.panTo([lat, lon], { animate: true, duration: FOLLOW_PAN_S })
      }
    }
    const release = window.setTimeout(() => {
      skipPanRef.current = false
    }, 300)
    return () => window.clearTimeout(release)
  }, [lat, lon, follow])

  if (lat == null || lon == null) {
    return null
  }
  return <div className="inspection-map" ref={rootRef} />
}
