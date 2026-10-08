import { useEffect, useRef, useState } from 'react'
import { MapLibreMap, NavigationControl, ScaleControl } from 'maplibre-gl'
import type {
  ErrorEvent,
  GeoJSONSource,
  LngLatBoundsLike,
  MapGeoJSONFeature,
} from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

import type { Drain, DrainRow } from '../types'

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

const DRAINS_URL = '/data/drains.geojson'
const DUMPSITE_URL = '/data/dumpsite.geojson'

const COLOURS = {
  RED: '#d1453b',
  AMBER: '#d99a08',
  GREEN: '#2f9e55',
  PENDING: '#8c99a6',
}

const DUMPSITE_FILL = '#6b8fa8'
const CLAIMED_ROUTE = '#2f6fed'
const ACTUAL_ROUTE = '#d1453b'

const configError =
  !region || !apiKey
    ? 'Set VITE_AWS_REGION and VITE_LOCATION_API_KEY in frontend/.env'
    : null

interface Props {
  drains: DrainRow[]
  selectedDrainId: string | null
  detail: Drain | null
  selectedTripId: string | null
  onSelect: (drainId: string) => void
}

type FeatureCollection = {
  features: Array<{ properties: Record<string, unknown>; geometry: { coordinates: number[][][] } }>
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

function lineFeature(coordinates: [number, number][]) {
  return {
    type: 'Feature' as const,
    properties: {},
    geometry: { type: 'LineString' as const, coordinates },
  }
}

const EMPTY = { type: 'FeatureCollection' as const, features: [] }

function MapView({ drains, selectedDrainId, detail, selectedTripId, onSelect }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const [ready, setReady] = useState(false)
  const [mapError, setMapError] = useState<string | null>(null)
  const error = configError ?? mapError

  // ---- create the map once
  useEffect(() => {
    if (!containerRef.current || configError) return

    const map = new MapLibreMap({
      container: containerRef.current,
      style: styleUrl,
      center,
      zoom,
    })
    mapRef.current = map

    map.addControl(new NavigationControl(), 'bottom-right')
    map.addControl(new ScaleControl(), 'bottom-left')
    map.on('error', (event: ErrorEvent) =>
      setMapError(event.error?.message ?? 'Map failed to load'),
    )

    const addLayers = async () => {
      const [drainGeo, dumpsite] = await Promise.all([
        fetch(DRAINS_URL).then((response) => response.json()),
        fetch(DUMPSITE_URL).then((response) => response.json()),
      ])

      map.addSource('drains', { type: 'geojson', data: drainGeo, promoteId: 'drainId' })
      map.addLayer({
        id: 'drains-fill',
        type: 'fill',
        source: 'drains',
        paint: {
          'fill-color': [
            'match',
            ['coalesce', ['feature-state', 'verdict'], 'PENDING'],
            'RED', COLOURS.RED,
            'AMBER', COLOURS.AMBER,
            'GREEN', COLOURS.GREEN,
            COLOURS.PENDING,
          ],
          'fill-opacity': [
            'case', ['boolean', ['feature-state', 'selected'], false], 0.85, 0.55,
          ],
        },
      })
      map.addLayer({
        id: 'drains-outline',
        type: 'line',
        source: 'drains',
        paint: {
          'line-color': '#15202b',
          'line-width': [
            'case', ['boolean', ['feature-state', 'selected'], false], 3, 1,
          ],
        },
      })
      map.addLayer({
        id: 'drains-label',
        type: 'symbol',
        source: 'drains',
        layout: { 'text-field': ['get', 'drainId'], 'text-size': 13 },
        paint: {
          'text-color': '#13202c',
          'text-halo-color': '#ffffff',
          'text-halo-width': 1.4,
        },
      })

      map.addSource('dumpsite', { type: 'geojson', data: dumpsite })
      map.addLayer({
        id: 'dumpsite-fill',
        type: 'fill',
        source: 'dumpsite',
        paint: { 'fill-color': DUMPSITE_FILL, 'fill-opacity': 0.3 },
      })
      map.addLayer({
        id: 'dumpsite-outline',
        type: 'line',
        source: 'dumpsite',
        paint: { 'line-color': DUMPSITE_FILL, 'line-width': 2, 'line-dasharray': [2, 1] },
      })

      // The two routes of the drill-down, empty until a drain is picked.
      map.addSource('claimed-route', { type: 'geojson', data: EMPTY })
      map.addLayer({
        id: 'claimed-route-line',
        type: 'line',
        source: 'claimed-route',
        paint: {
          'line-color': CLAIMED_ROUTE,
          'line-width': 3,
          'line-dasharray': [2, 1.5],
          'line-opacity': 0.9,
        },
      })

      map.addSource('actual-route', { type: 'geojson', data: EMPTY })
      map.addLayer({
        id: 'actual-route-line',
        type: 'line',
        source: 'actual-route',
        paint: { 'line-color': ACTUAL_ROUTE, 'line-width': 4, 'line-opacity': 0.95 },
      })

      const bounds = boundsOf([drainGeo, dumpsite])
      if (bounds) map.fitBounds(bounds, { padding: 64, duration: 0 })

      map.on('click', 'drains-fill', (event) => {
        const feature = event.features?.[0] as MapGeoJSONFeature | undefined
        const drainId = feature?.properties?.drainId
        if (drainId) onSelect(String(drainId))
      })
      map.on('mouseenter', 'drains-fill', () => {
        map.getCanvas().style.cursor = 'pointer'
      })
      map.on('mouseleave', 'drains-fill', () => {
        map.getCanvas().style.cursor = ''
      })

      setReady(true)
    }

    map.on('load', () => {
      addLayers().catch((cause: unknown) =>
        setMapError(
          'Could not load the drain geometry. Run data/osm_drains.py and copy ' +
            `drains.geojson into frontend/public/data. (${String(cause)})`,
        ),
      )
    })

    return () => {
      mapRef.current = null
      map.remove()
    }
    // onSelect is stable for the life of the app.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ---- colour the polygons by verdict
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return

    for (const drain of drains) {
      map.setFeatureState(
        { source: 'drains', id: drain.drainId },
        { verdict: drain.verdict ?? 'PENDING', selected: drain.drainId === selectedDrainId },
      )
    }
  }, [drains, selectedDrainId, ready])

  // ---- draw the claimed and actual routes for the selected trip
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return

    const claimed = map.getSource('claimed-route') as GeoJSONSource | undefined
    const actual = map.getSource('actual-route') as GeoJSONSource | undefined
    if (!claimed || !actual) return

    if (!detail) {
      claimed.setData(EMPTY)
      actual.setData(EMPTY)
      return
    }

    const trip =
      detail.trips.find((entry) => entry.tripId === selectedTripId) ?? detail.trips[0]

    claimed.setData(
      detail.claimedRoute
        ? { type: 'FeatureCollection', features: [lineFeature(detail.claimedRoute)] }
        : EMPTY,
    )
    actual.setData(
      trip && trip.actualRoute.length > 1
        ? { type: 'FeatureCollection', features: [lineFeature(trip.actualRoute)] }
        : EMPTY,
    )

    const points = [...(detail.claimedRoute ?? []), ...(trip?.actualRoute ?? [])]
    if (points.length > 1) {
      const lons = points.map(([lon]) => lon)
      const lats = points.map(([, lat]) => lat)
      map.fitBounds(
        [
          [Math.min(...lons), Math.min(...lats)],
          [Math.max(...lons), Math.max(...lats)],
        ],
        { padding: 90, duration: 600 },
      )
    }
  }, [detail, selectedTripId, ready])

  return (
    <div className="map-wrap">
      <div ref={containerRef} className="map" />

      <div className="map-legend">
        <span className="swatch" style={{ background: COLOURS.GREEN }} /> verified
        <span className="swatch" style={{ background: COLOURS.AMBER }} /> review
        <span className="swatch" style={{ background: COLOURS.RED }} /> hold
        <span className="legend-divider" />
        <span className="line-key line-claimed" /> claimed route
        <span className="line-key line-actual" /> actual GPS
      </div>

      {error && <div className="map-error">{error}</div>}
    </div>
  )
}

export default MapView
