import { useEffect, useRef, useState } from 'react'
import { MapLibreMap, NavigationControl, ScaleControl } from 'maplibre-gl'
import type { ErrorEvent } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

const region = import.meta.env.VITE_AWS_REGION
const apiKey = import.meta.env.VITE_LOCATION_API_KEY
const center: [number, number] = [
  Number(import.meta.env.VITE_MAP_CENTER_LON),
  Number(import.meta.env.VITE_MAP_CENTER_LAT),
]
const zoom = Number(import.meta.env.VITE_MAP_ZOOM ?? 13)

// Amazon Location Maps v2 serves styles straight from an API key, so there is
// no map resource to create.
const styleUrl = `https://maps.geo.${region}.amazonaws.com/v2/styles/Standard/descriptor?key=${apiKey}&color-scheme=Light`

// Missing env is knowable before render, so it is not effect state.
const configError =
  !region || !apiKey
    ? 'Set VITE_AWS_REGION and VITE_LOCATION_API_KEY in frontend/.env'
    : null

function MapView() {
  const containerRef = useRef<HTMLDivElement>(null)
  const [mapError, setMapError] = useState<string | null>(null)
  const error = configError ?? mapError

  useEffect(() => {
    if (!containerRef.current || configError) return

    const map = new MapLibreMap({
      container: containerRef.current,
      style: styleUrl,
      center,
      zoom,
    })

    map.addControl(new NavigationControl(), 'bottom-right')
    map.addControl(new ScaleControl(), 'bottom-left')
    map.on('error', (event: ErrorEvent) =>
      setMapError(event.error?.message ?? 'Map failed to load'),
    )

    // TODO Day 1 afternoon: render the 18 drain polygons and the dump site.
    return () => map.remove()
  }, [])

  return (
    <div className="map-wrap">
      <div ref={containerRef} className="map" />
      {error && <div className="map-error">{error}</div>}
    </div>
  )
}

export default MapView
