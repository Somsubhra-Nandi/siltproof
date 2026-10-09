// The trial's map: the same MapLibre and basemap set-up as the investigation
// (Amazon Location when configured, the bundled offline style otherwise), with
// one GeoJSON source holding everything the judge supplied.

import { useEffect, useMemo, useRef, useState } from 'react'
import { AttributionControl, MapLibreMap } from 'maplibre-gl'
import type { GeoJSONSource, MapMouseEvent } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import type * as GeoJSON from 'geojson'
import { fallBackOnError, fallbackStyle, initialMode, loadBasemap, startingStyleUrl } from '../../basemap'
import { trialFeatures } from './mapData'
import type { DisposalSite, DrainLocation, LonLat } from './types'

export type PickMode = 'drain' | 'disposal' | null

type Props = {
  drain?: DrainLocation
  disposal?: DisposalSite
  photos: Array<{ id: string; point: LonLat; role: string }>
  routes: Array<{ id: string; route: LonLat[] }>
  pickMode: PickMode
  onPick: (mode: Exclude<PickMode, null>, point: LonLat) => void
}

const center: LonLat = [
  Number(import.meta.env.VITE_MAP_CENTER_LON) || 88.4712,
  Number(import.meta.env.VITE_MAP_CENTER_LAT) || 22.5801,
]

function bounds(collection: GeoJSON.FeatureCollection): [[number, number], [number, number]] | null {
  let west = Infinity
  let south = Infinity
  let east = -Infinity
  let north = -Infinity
  const visit = (value: unknown) => {
    if (Array.isArray(value) && typeof value[0] === 'number') {
      west = Math.min(west, value[0] as number)
      east = Math.max(east, value[0] as number)
      south = Math.min(south, value[1] as number)
      north = Math.max(north, value[1] as number)
    } else if (Array.isArray(value)) value.forEach(visit)
  }
  for (const feature of collection.features) {
    if (feature.properties?.kind === 'tolerance') continue
    visit((feature.geometry as { coordinates: unknown }).coordinates)
  }
  return Number.isFinite(west) ? [[west, south], [east, north]] : null
}

function fitTo(map: MapLibreMap, box: [[number, number], [number, number]] | null, duration: number) {
  if (!box) return
  const [[west, south], [east, north]] = box
  if (west === east && south === north) map.easeTo({ center: [west, south], zoom: 16, duration })
  else map.fitBounds(box, { padding: 60, maxZoom: 17, duration })
}

const INK = '#1c2a38'
const PRUSSIAN = '#1f4d78'
const VERIFIED = '#39684f'
const REVIEW = '#a6731e'

function addLayers(map: MapLibreMap, data: GeoJSON.FeatureCollection) {
  if (map.getSource('trial')) {
    ;(map.getSource('trial') as GeoJSONSource).setData(data)
    return
  }
  map.addSource('trial', { type: 'geojson', data })
  const kind = (name: string) => ['==', ['get', 'kind'], name] as unknown as never
  map.addLayer({ id: 'trial-disposal-area', type: 'fill', source: 'trial', filter: kind('disposal-area'), paint: { 'fill-color': VERIFIED, 'fill-opacity': 0.12 } })
  map.addLayer({ id: 'trial-disposal-edge', type: 'line', source: 'trial', filter: kind('disposal-area'), paint: { 'line-color': VERIFIED, 'line-width': 1.5, 'line-dasharray': [3, 2] } })
  map.addLayer({ id: 'trial-tolerance', type: 'fill', source: 'trial', filter: kind('tolerance'), paint: { 'fill-color': PRUSSIAN, 'fill-opacity': 0.08 } })
  map.addLayer({ id: 'trial-tolerance-edge', type: 'line', source: 'trial', filter: kind('tolerance'), paint: { 'line-color': PRUSSIAN, 'line-width': 1, 'line-dasharray': [2, 2] } })
  map.addLayer({ id: 'trial-trace', type: 'line', source: 'trial', filter: kind('trace'), paint: { 'line-color': REVIEW, 'line-width': 2.5 } })
  map.addLayer({ id: 'trial-drain-line', type: 'line', source: 'trial', filter: kind('drain-line'), paint: { 'line-color': PRUSSIAN, 'line-width': 3, 'line-dasharray': [4, 2] } })
  map.addLayer({ id: 'trial-photo', type: 'circle', source: 'trial', filter: kind('photo'), paint: { 'circle-radius': 5, 'circle-color': '#fbfaf6', 'circle-stroke-color': INK, 'circle-stroke-width': 2 } })
  map.addLayer({ id: 'trial-drain-point', type: 'circle', source: 'trial', filter: kind('drain-point'), paint: { 'circle-radius': 7, 'circle-color': PRUSSIAN, 'circle-stroke-color': '#fbfaf6', 'circle-stroke-width': 2 } })
  map.addLayer({ id: 'trial-disposal-point', type: 'circle', source: 'trial', filter: kind('disposal-point'), paint: { 'circle-radius': 6, 'circle-color': VERIFIED, 'circle-stroke-color': '#fbfaf6', 'circle-stroke-width': 2 } })
}

export default function TrialMap(props: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const dataRef = useRef<GeoJSON.FeatureCollection | null>(null)
  const pickRef = useRef(props)
  const boxRef = useRef<[[number, number], [number, number]] | null>(null)
  // Set once the first style has loaded. isStyleLoaded() is false while any
  // GeoJSON source is re-parsing, which is exactly when new data arrives.
  const readyRef = useRef(false)
  const [offlineBasemap, setOfflineBasemap] = useState(initialMode() === 'fallback')
  const [error, setError] = useState<string | null>(null)

  const { drain, disposal, photos, routes } = props
  const data = useMemo(() => trialFeatures({ drain, disposal, photos, routes }), [drain, disposal, photos, routes])

  useEffect(() => {
    pickRef.current = props
  })

  useEffect(() => {
    if (!containerRef.current) return
    let swapped = false
    const startUrl = startingStyleUrl()
    const map = new MapLibreMap({
      container: containerRef.current,
      style: startUrl ?? fallbackStyle(),
      center,
      zoom: 14,
      attributionControl: false,
    })
    mapRef.current = map
    map.addControl(new AttributionControl({ compact: true }), 'bottom-left')

    const draw = () => {
      if (dataRef.current && map.isStyleLoaded()) addLayers(map, dataRef.current)
    }
    map.on('style.load', draw)
    // A slow or failed tile after the style loaded is not a failed style
    // (fallBackOnError); only then would the offline basemap be wanted.
    let styleLoaded = false
    map.once('style.load', () => {
      styleLoaded = true
    })
    map.on('load', () => {
      readyRef.current = true
      draw()
      fitTo(map, boxRef.current, 0)
    })
    // The container is laid out by CSS (sticky column, stacked on mobile), so
    // keep the canvas in step with it.
    const observer = new ResizeObserver(() => map.resize())
    observer.observe(containerRef.current)
    map.on('error', (event) => {
      const status = (event.error as { status?: number } | undefined)?.status
      if (!swapped && startUrl && fallBackOnError(styleLoaded, status)) {
        swapped = true
        setOfflineBasemap(true)
        loadBasemap().then((basemap) => map.setStyle(fallbackStyle(basemap)))
        return
      }
      if (swapped || !startUrl) setError(event.error?.message ?? 'map error')
    })
    if (!startUrl) {
      loadBasemap().then((basemap) => {
        if (basemap) (map.getSource('basemap') as GeoJSONSource | undefined)?.setData(basemap as never)
      })
    }
    map.on('click', (event: MapMouseEvent) => {
      const { pickMode, onPick } = pickRef.current
      if (pickMode) onPick(pickMode, [Number(event.lngLat.lng.toFixed(7)), Number(event.lngLat.lat.toFixed(7))])
    })
    return () => {
      observer.disconnect()
      map.remove()
      mapRef.current = null
    }
  }, [])

  const box = bounds(data)
  const boxKey = box ? box.flat().map((value) => value.toFixed(5)).join(',') : ''
  useEffect(() => {
    dataRef.current = data
    const map = mapRef.current
    if (!map) return
    if (readyRef.current && map.getStyle()) {
      try {
        addLayers(map, data)
      } catch {
        // Mid style swap; 'style.load' redraws from dataRef.
      }
    }
  }, [data])

  useEffect(() => {
    // The first evidence can be a city away from the default centre; flying
    // there requests (and then aborts) every tile on the way, so jump.
    const first = boxRef.current === null
    boxRef.current = box
    const map = mapRef.current
    if (!map || !box || !readyRef.current) return
    fitTo(map, box, first ? 0 : 400)
    // Refit only when the extent of the data changes, not on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [boxKey])

  return (
    <div className={`jt-map${props.pickMode ? ' is-picking' : ''}`}>
      <div ref={containerRef} className="jt-map-canvas" aria-label="Trial map" role="region" />
      {props.pickMode && (
        <p className="jt-map-hint" role="status">
          Click the map to place the {props.pickMode === 'drain' ? 'drain' : 'disposal site'}.
        </p>
      )}
      <ul className="jt-map-key" aria-label="Map key">
        <li><i className="k-drain" /> Drain location (supplied)</li>
        <li><i className="k-disposal" /> Designated disposal site</li>
        <li><i className="k-photo" /> Photo GPS</li>
        <li><i className="k-trace" /> Truck trace</li>
      </ul>
      {offlineBasemap && <p className="jt-map-note">Offline basemap: streets may be missing here.</p>}
      {error && <p className="jt-map-note err">Map: {error}</p>}
    </div>
  )
}
