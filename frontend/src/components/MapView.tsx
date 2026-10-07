import { useEffect, useRef, useState } from 'react'
import { MapLibreMap, NavigationControl, ScaleControl } from 'maplibre-gl'
import type { ErrorEvent, LngLatBoundsLike } from 'maplibre-gl'
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

// Written by data/osm_drains.py and copied into public/data.
const DRAINS_URL = '/data/drains.geojson'
const DUMPSITE_URL = '/data/dumpsite.geojson'

// Every drain is neutral grey today. Day 2 colours them by verdict.
const DRAIN_FILL = '#8c99a6'
const DRAIN_LINE = '#5b6773'
const DUMPSITE_FILL = '#6b8fa8'

// Missing env is knowable before render, so it is not effect state.
const configError =
  !region || !apiKey
    ? 'Set VITE_AWS_REGION and VITE_LOCATION_API_KEY in frontend/.env'
    : null

type FeatureCollection = {
  features: Array<{ geometry: { coordinates: number[][][] } }>
}

function boundsOf(collections: FeatureCollection[]): LngLatBoundsLike | null {
  let west = Infinity
  let south = Infinity
  let east = -Infinity
  let north = -Infinity

  for (const collection of collections) {
    for (const feature of collection.features) {
      for (const ring of feature.geometry.coordinates) {
        for (const [lon, lat] of ring) {
          west = Math.min(west, lon)
          south = Math.min(south, lat)
          east = Math.max(east, lon)
          north = Math.max(north, lat)
        }
      }
    }
  }

  if (!Number.isFinite(west)) return null
  return [
    [west, south],
    [east, north],
  ]
}

function MapView() {
  const containerRef = useRef<HTMLDivElement>(null)
  const [mapError, setMapError] = useState<string | null>(null)
  const [drainCount, setDrainCount] = useState<number | null>(null)
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

    let cancelled = false

    const addLayers = async () => {
      const [drains, dumpsite] = await Promise.all([
        fetch(DRAINS_URL).then((response) => response.json()),
        fetch(DUMPSITE_URL).then((response) => response.json()),
      ])
      if (cancelled) return

      map.addSource('drains', { type: 'geojson', data: drains })
      map.addLayer({
        id: 'drains-fill',
        type: 'fill',
        source: 'drains',
        paint: { 'fill-color': DRAIN_FILL, 'fill-opacity': 0.45 },
      })
      map.addLayer({
        id: 'drains-outline',
        type: 'line',
        source: 'drains',
        paint: { 'line-color': DRAIN_LINE, 'line-width': 1.5 },
      })
      map.addLayer({
        id: 'drains-label',
        type: 'symbol',
        source: 'drains',
        layout: {
          'text-field': ['get', 'drainId'],
          'text-size': 13,
          'text-allow-overlap': false,
        },
        paint: {
          'text-color': '#1d262f',
          'text-halo-color': '#ffffff',
          'text-halo-width': 1.4,
        },
      })

      map.addSource('dumpsite', { type: 'geojson', data: dumpsite })
      map.addLayer({
        id: 'dumpsite-fill',
        type: 'fill',
        source: 'dumpsite',
        paint: { 'fill-color': DUMPSITE_FILL, 'fill-opacity': 0.35 },
      })
      map.addLayer({
        id: 'dumpsite-outline',
        type: 'line',
        source: 'dumpsite',
        paint: { 'line-color': DUMPSITE_FILL, 'line-width': 2, 'line-dasharray': [2, 1] },
      })

      const bounds = boundsOf([drains, dumpsite])
      if (bounds) map.fitBounds(bounds, { padding: 64, duration: 0 })

      setDrainCount(drains.features.length)
    }

    map.on('load', () => {
      addLayers().catch((cause: unknown) =>
        setMapError(
          `Could not load the drain geometry. Run data/osm_drains.py and copy ` +
            `drains.geojson into frontend/public/data. (${String(cause)})`,
        ),
      )
    })

    return () => {
      cancelled = true
      map.remove()
    }
  }, [])

  return (
    <div className="map-wrap">
      <div ref={containerRef} className="map" />

      {drainCount !== null && (
        <div className="map-legend">
          <span className="swatch swatch-drain" />
          {drainCount} drain sections
          <span className="swatch swatch-dump" />
          approved dump site
        </div>
      )}

      {error && <div className="map-error">{error}</div>}
    </div>
  )
}

export default MapView
