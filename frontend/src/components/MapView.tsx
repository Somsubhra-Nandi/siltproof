import { useEffect, useRef, useState } from 'react'
import { MapLibreMap, NavigationControl, ScaleControl } from 'maplibre-gl'
import type { ErrorEvent, GeoJSONSource, MapGeoJSONFeature } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

import {
  basemapAttribution,
  boundsOf,
  COLOURS,
  fallbackStyle,
  initialMode,
  loadBasemap,
  locationConfigured,
  startingStyleUrl,
} from '../basemap'
import type { BasemapMode, FeatureCollection } from '../basemap'
import type { Drain, DrainRow } from '../types'

const center: [number, number] = [
  Number(import.meta.env.VITE_MAP_CENTER_LON) || 72.8777,
  Number(import.meta.env.VITE_MAP_CENTER_LAT) || 19.076,
]
const zoom = Number(import.meta.env.VITE_MAP_ZOOM) || 13

const DRAINS_URL = '/data/drains.geojson'
const DUMPSITE_URL = '/data/dumpsite.geojson'

// If Amazon Location has not answered by now, stop waiting and draw something.
const STYLE_TIMEOUT_MS = 6000

// Drains colour in one after another when a verification finishes.
const COLOUR_IN_STEP_MS = 45

const DUMPSITE_FILL = '#6b8fa8'
const CLAIMED_ROUTE = '#2f6fed'
const ACTUAL_ROUTE = '#d1453b'

interface Props {
  drains: DrainRow[]
  selectedDrainId: string | null
  detail: Drain | null
  selectedTripId: string | null
  animate: boolean
  onSelect: (drainId: string) => void
}

function lineFeature(coordinates: [number, number][]) {
  return {
    type: 'Feature' as const,
    properties: {},
    geometry: { type: 'LineString' as const, coordinates },
  }
}

const EMPTY = { type: 'FeatureCollection' as const, features: [] }

function MapView({
  drains,
  selectedDrainId,
  detail,
  selectedTripId,
  animate,
  onSelect,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const geometryRef = useRef<{ drains: unknown; dumpsite: unknown } | null>(null)
  const basemapRef = useRef<unknown>(null)
  const swappedRef = useRef(false)
  const selectRef = useRef(onSelect)

  // Kept in a ref so the map's click handler always calls the current
  // callback without the map having to be rebuilt.
  useEffect(() => {
    selectRef.current = onSelect
  }, [onSelect])

  const [mode, setMode] = useState<BasemapMode>(() => initialMode())
  const [layersReady, setLayersReady] = useState(0)
  const [attribution, setAttribution] = useState<string | null>(null)
  const [mapError, setMapError] = useState<string | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    let cancelled = false
    const startMode = initialMode()

    const startingStyle = startingStyleUrl()

    const map = new MapLibreMap({
      container: containerRef.current,
      style: startingStyle ?? fallbackStyle(),
      center,
      zoom,
      attributionControl: false,
    })
    mapRef.current = map

    map.addControl(new NavigationControl(), 'bottom-right')
    map.addControl(new ScaleControl(), 'bottom-left')

    /**
     * Drop to the offline style. Called when Amazon Location is not
     * configured, errors, or simply never answers. Data layers are re-added
     * by the styledata handler below, so the drains survive the swap.
     */
    const swapToFallback = (why: string) => {
      if (cancelled || swappedRef.current) return
      swappedRef.current = true
      setMode('fallback')
      setMapError(null)
      console.info(`[siltproof] offline basemap: ${why}`)
      map.setStyle(fallbackStyle(basemapRef.current))
    }

    map.on('error', (event: ErrorEvent) => {
      const text = event.error?.message ?? 'map error'
      if (!swappedRef.current && startMode === 'location') {
        swapToFallback(`Amazon Location style failed (${text})`)
        return
      }
      // In fallback mode there is nothing left to fall back to, so a problem
      // here is worth showing - but it must never blank the screen.
      setMapError(text)
    })

    // Fetch the backdrop and the ward geometry in parallel with the style.
    const loading = Promise.all([
      loadBasemap(),
      fetch(DRAINS_URL).then((response) => response.json()),
      fetch(DUMPSITE_URL).then((response) => response.json()),
    ])
      .then(([basemap, drainGeo, dumpsite]) => {
        if (cancelled) return
        basemapRef.current = basemap
        geometryRef.current = { drains: drainGeo, dumpsite }
        setAttribution(basemapAttribution(basemap))

        // The fallback style was created before the backdrop arrived, so give
        // it the real data now.
        if (swappedRef.current || startMode === 'fallback') {
          const source = map.getSource('basemap') as GeoJSONSource | undefined
          if (source && basemap) source.setData(basemap as never)
        }

        addDataLayers()
      })
      .catch((cause: unknown) =>
        setMapError(
          'Could not load the drain geometry. Run data/osm_drains.py and copy ' +
            `drains.geojson into frontend/public/data. (${String(cause)})`,
        ),
      )

    /**
     * Add the drains, dump site and route layers.
     *
     * Deliberately independent of which basemap won: it runs once the style
     * is ready in either mode, and again after a style swap, because
     * setStyle throws every source and layer away.
     */
    const addDataLayers = () => {
      if (cancelled || !map.isStyleLoaded() || !geometryRef.current) return
      if (map.getSource('drains')) return

      const { drains: drainGeo, dumpsite } = geometryRef.current

      map.addSource('drains', {
        type: 'geojson',
        data: drainGeo as never,
        promoteId: 'drainId',
      })
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
            'case', ['boolean', ['feature-state', 'selected'], false], 0.9, 0.6,
          ],
          'fill-opacity-transition': { duration: 350, delay: 0 },
          'fill-color-transition': { duration: 350, delay: 0 },
        },
      })
      map.addLayer({
        id: 'drains-outline',
        type: 'line',
        source: 'drains',
        paint: {
          'line-color': '#15202b',
          'line-width': ['case', ['boolean', ['feature-state', 'selected'], false], 3, 1],
        },
      })

      map.addSource('dumpsite', { type: 'geojson', data: dumpsite as never })
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

      map.addSource('claimed-route', { type: 'geojson', data: EMPTY })
      map.addLayer({
        id: 'claimed-route-line',
        type: 'line',
        source: 'claimed-route',
        paint: {
          'line-color': CLAIMED_ROUTE,
          'line-width': 3,
          'line-dasharray': [2, 1.5],
        },
      })

      map.addSource('actual-route', { type: 'geojson', data: EMPTY })
      map.addLayer({
        id: 'actual-route-line',
        type: 'line',
        source: 'actual-route',
        paint: { 'line-color': ACTUAL_ROUTE, 'line-width': 4 },
      })

      const bounds = boundsOf([
        drainGeo as FeatureCollection,
        dumpsite as FeatureCollection,
      ])
      if (bounds) map.fitBounds(bounds, { padding: 64, duration: 0 })

      // Tell the rest of the component the layers exist again.
      setLayersReady((count) => count + 1)
    }

    map.on('load', addDataLayers)
    // Fires again after setStyle, when the new style has been parsed.
    map.on('styledata', addDataLayers)

    // Registered once. Inside addDataLayers these would be added again on
    // every style swap, and each click would open the drain twice.
    map.on('click', 'drains-fill', (event) => {
      const feature = event.features?.[0] as MapGeoJSONFeature | undefined
      const drainId = feature?.properties?.drainId
      if (drainId) selectRef.current(String(drainId))
    })
    map.on('mouseenter', 'drains-fill', () => {
      map.getCanvas().style.cursor = 'pointer'
    })
    map.on('mouseleave', 'drains-fill', () => {
      map.getCanvas().style.cursor = ''
    })

    const timeout = window.setTimeout(() => {
      if (!cancelled && !map.isStyleLoaded()) {
        swapToFallback('Amazon Location style did not load in time')
      }
    }, STYLE_TIMEOUT_MS)

    return () => {
      cancelled = true
      window.clearTimeout(timeout)
      void loading
      mapRef.current = null
      map.remove()
    }
  }, [])

  // ---- colour the drains by verdict
  useEffect(() => {
    const map = mapRef.current
    if (!map || !layersReady || !map.getSource('drains')) return

    const paint = (drain: DrainRow) =>
      map.setFeatureState(
        { source: 'drains', id: drain.drainId },
        {
          verdict: drain.verdict ?? 'PENDING',
          selected: drain.drainId === selectedDrainId,
        },
      )

    if (!animate) {
      drains.forEach(paint)
      return
    }

    // Colour them in one by one, so a verification reads as something that
    // happened rather than a jump cut.
    const timers = drains.map((drain, index) =>
      window.setTimeout(() => paint(drain), index * COLOUR_IN_STEP_MS),
    )
    return () => timers.forEach(window.clearTimeout)
  }, [drains, selectedDrainId, layersReady, animate])

  // ---- the two routes of the drill-down
  useEffect(() => {
    const map = mapRef.current
    if (!map || !layersReady) return

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
  }, [detail, selectedTripId, layersReady])

  return (
    <div className="map-wrap">
      <div ref={containerRef} className="map" data-testid="map" />

      <div className="map-legend">
        <span className="swatch" style={{ background: COLOURS.GREEN }} /> verified
        <span className="swatch" style={{ background: COLOURS.AMBER }} /> review
        <span className="swatch" style={{ background: COLOURS.RED }} /> hold
        <span className="legend-divider" />
        <span className="line-key line-claimed" /> claimed route
        <span className="line-key line-actual" /> actual GPS
      </div>

      {mode === 'fallback' && (
        <div className="map-mode" title={
          locationConfigured
            ? 'Amazon Location did not load, so the bundled offline style is being used.'
            : 'No Amazon Location API key configured, so the bundled offline style is being used.'
        }>
          Offline map (dev)
        </div>
      )}

      {attribution && mode === 'fallback' && (
        <div className="map-attribution">{attribution}</div>
      )}

      {mapError && <div className="map-note">{mapError}</div>}
    </div>
  )
}

export default MapView
