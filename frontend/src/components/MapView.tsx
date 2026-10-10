import { useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import type { CSSProperties, Ref } from 'react'
import { AttributionControl, MapLibreMap, Marker } from 'maplibre-gl'
import type { ErrorEvent, GeoJSONSource, LngLatBoundsLike } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import type * as GeoJSON from 'geojson'

import {
  basemapAttribution,
  COLOURS,
  fallBackOnError,
  fallbackStyle,
  initialMode,
  isAmazonLocationUrl,
  loadBasemap,
  locationConfigured,
  startingStyleUrl,
  surveyTint,
} from '../basemap'
import type { BasemapMode } from '../basemap'
import { bearingTo, metres, sliceLine } from '../lib/caseFacts'
import type { CaseFacts, LngLat } from '../lib/caseFacts'
import { ease, tween, wait } from '../lib/motion'
import { grouped, lakh, rupees } from '../format'
import type { Drain, DrainRow } from '../types'

const center: LngLat = [
  Number(import.meta.env.VITE_MAP_CENTER_LON) || 72.8777,
  Number(import.meta.env.VITE_MAP_CENTER_LAT) || 19.076,
]
const zoom = Number(import.meta.env.VITE_MAP_ZOOM) || 13

const DRAINS_URL = '/data/drains.geojson'
const DUMPSITE_URL = '/data/dumpsite.geojson'

// If Amazon Location has not answered by now, stop waiting and draw something.
const STYLE_TIMEOUT_MS = 6000

const INK = '#1c2a38'
const PAPER = '#f2efe7'
const RING_WIDTH = 5

// Timings for the drain-opening flight (DESIGN.md, shortened to 4-6 s).
const TILT_MS = 1000
const REPLAY_MS = 2600
const DIMENSION_MS = 500
const HOLD_MS = 400
// A replay in the case file holds its ending long enough to read.
const REPLAY_HOLD_MS = 1600

export type MapMode = 'overview' | 'flying' | 'case'

export interface MapHandle {
  /** Sweep top to bottom, calling `onPass` as the line passes each drain. */
  sweep(onPass: (drainIds: string[], progress: number) => void, ms?: number): Promise<void>
  /** Tilt over the drain and replay the focus trip's trace, once. */
  fly(drain: Drain, facts: CaseFacts): Promise<void>
  /** In the case file: redraw the selected trip's recorded trace, then restore the case map. */
  replay(drain: Drain, facts: CaseFacts): Promise<void>
}

type Anchor = 'right' | 'left' | 'bottom' | 'center'
type Label = { marker: Marker; at: LngLat; html: string; cls: string; anchor: Anchor; offset: [number, number] }

// Keep case labels this far inside the map frame.
const LABEL_INSET = 6

interface Props {
  ref?: Ref<MapHandle>
  rows: DrainRow[]
  /** Drains whose verdict may be shown; null means all of them. */
  revealed: Set<string> | null
  verified: boolean
  hoverId: string | null
  mode: MapMode
  caseDrain: Drain | null
  facts: CaseFacts | null
  reasons: Record<string, string>
  rate: number
  reducedMotion: boolean
  onHover: (drainId: string | null) => void
  onSelect: (drainId: string) => void
}

type Furniture = {
  ticks: Array<{ key: string; style: CSSProperties; text: string; side: 'top' | 'left' }>
  scale: { width: number; text: string }
  bearing: number
  offmap: { left: number; top: number; angle: number; km: string } | null
  tip: { x: number; y: number; row: DrainRow; left: boolean } | null
}

const EMPTY_FURNITURE: Furniture = { ticks: [], scale: { width: 80, text: '' }, bearing: 0, offmap: null, tip: null }

type Geo = {
  drains: GeoJSON.FeatureCollection
  dumpsite: GeoJSON.FeatureCollection
  centroids: Record<string, LngLat>
  dump: LngLat
}

const EMPTY: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] }

function line(coordinates: LngLat[]): GeoJSON.Feature {
  return { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates } }
}

function outerRing(geometry: GeoJSON.Geometry): LngLat[] {
  if (geometry.type === 'Polygon') return geometry.coordinates[0] as LngLat[]
  if (geometry.type === 'MultiPolygon') return geometry.coordinates[0][0] as LngLat[]
  if (geometry.type === 'LineString') return geometry.coordinates as LngLat[]
  return []
}

function centroidOf(geometry: GeoJSON.Geometry): LngLat {
  const ring = outerRing(geometry)
  const sum = ring.reduce((acc, [x, y]) => [acc[0] + x, acc[1] + y], [0, 0])
  return [sum[0] / ring.length, sum[1] / ring.length]
}

function boundsOfPoints(points: LngLat[]): [LngLat, LngLat] {
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity
  for (const [x, y] of points) {
    w = Math.min(w, x); s = Math.min(s, y); e = Math.max(e, x); n = Math.max(n, y)
  }
  return [[w, s], [e, n]]
}

/** Ring radius: area is proportional to tonnes billed. */
const ringRadius = (tonnes: number) => 6 + 2.1 * Math.sqrt(tonnes)

function ringSvg(row: DrainRow, shown: boolean) {
  const r = ringRadius(row.claimedTonnes)
  const size = (r + 4) * 2
  const c = size / 2
  const rr = r - RING_WIDTH / 2
  const circ = 2 * Math.PI * rr
  let arcs = ''
  if (shown && row.verdict) {
    let offset = 0
    const parts: Array<[number, string]> = [
      [row.verifiedTonnes, COLOURS.GREEN],
      [row.reviewTonnes, COLOURS.AMBER],
      [row.heldTonnes, COLOURS.RED],
    ]
    for (const [tonnes, colour] of parts) {
      if (!tonnes) continue
      const len = (tonnes / row.claimedTonnes) * circ
      arcs += `<circle cx="${c}" cy="${c}" r="${rr}" fill="none" stroke="${colour}" stroke-width="${RING_WIDTH}" stroke-dasharray="${len} ${circ}" stroke-dashoffset="${-offset}" transform="rotate(-90 ${c} ${c})"/>`
      offset += len
    }
  } else {
    arcs = `<circle cx="${c}" cy="${c}" r="${rr}" fill="none" stroke="${COLOURS.PENDING}" stroke-width="${RING_WIDTH}" stroke-dasharray="3 2"/>`
  }
  return `<svg class="ring" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true"><circle class="halo" cx="${c}" cy="${c}" r="${r + 1}" fill="${PAPER}"/>${arcs}<text x="${c}" y="${c + 4.5}" text-anchor="middle">${row.drainId}</text></svg>`
}

function hatchImage() {
  const size = 8
  const data = new Uint8Array(size * size * 4)
  // A 45-degree hatch, drawn pixel by pixel so it needs no canvas.
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const on = (x + y) % size === 0 || (x + y) % size === 1
      const i = (y * size + x) * 4
      data[i] = 0x1c; data[i + 1] = 0x2a; data[i + 2] = 0x38
      data[i + 3] = on ? 200 : 0
    }
  }
  return { width: size, height: size, data }
}

/** A survey dimension line from the stop towards the dump site, with end ticks. */
function dimensionLine(a: LngLat, b: LngLat, frac: number): GeoJSON.FeatureCollection {
  const end: LngLat = [a[0] + (b[0] - a[0]) * frac, a[1] + (b[1] - a[1]) * frac]
  const k = Math.cos((a[1] * Math.PI) / 180)
  const dx = (b[0] - a[0]) * k
  const dy = b[1] - a[1]
  const len = Math.hypot(dx, dy) || 1
  const nx = ((-dy / len) * 0.0012) / k
  const ny = (dx / len) * 0.0012
  const tick = (p: LngLat) => line([[p[0] - nx, p[1] - ny], [p[0] + nx, p[1] + ny]])
  return {
    type: 'FeatureCollection',
    features: [line([a, end]), tick(a), ...(frac >= 1 ? [tick(b)] : [])],
  }
}

function MapView({
  ref,
  rows,
  revealed,
  verified,
  hoverId,
  mode,
  caseDrain,
  facts,
  reasons,
  rate,
  reducedMotion,
  onHover,
  onSelect,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const sweepRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const geoRef = useRef<Geo | null>(null)
  const basemapRef = useRef<unknown>(null)
  const swappedRef = useRef(false)
  const ringsRef = useRef<Record<string, { el: HTMLDivElement; tag: HTMLDivElement; markers: Marker[] }>>({})
  const labelsRef = useRef<Record<string, Label>>({})
  // Bumped to cancel a trace replay that is still drawing.
  const runRef = useRef(0)
  const fitLabelsRef = useRef<() => void>(() => undefined)
  const callbacks = useRef({ onSelect, onHover })
  const modeRef = useRef(mode)

  useEffect(() => {
    callbacks.current = { onSelect, onHover }
    modeRef.current = mode
  })

  const [basemapMode, setBasemapMode] = useState<BasemapMode>(() => initialMode())
  const [layersReady, setLayersReady] = useState(0)
  const [geoReady, setGeoReady] = useState(false)
  const [attribution, setAttribution] = useState<string | null>(null)
  const [mapError, setMapError] = useState<string | null>(null)
  // Bumped on every camera move, so the furniture (ticks, scale, north arrow,
  // tooltip, off-map pointer) follows the map.
  const [camera, setCamera] = useState(0)
  const [replay, setReplay] = useState<{ trip: string; left: string; km: string; done: string | null } | null>(null)

  // ------------------------------------------------------------ the map
  useEffect(() => {
    if (!containerRef.current) return
    let cancelled = false
    const startMode = initialMode()

    const startUrl = startingStyleUrl()
    const map = new MapLibreMap({
      container: containerRef.current,
      style: startUrl ?? fallbackStyle(),
      center,
      zoom,
      maxPitch: 70,
      attributionControl: false,
    })
    mapRef.current = map
    // Amazon Location's descriptor carries its data attribution, which must
    // be shown; the offline style has none, so this stays empty there.
    map.addControl(new AttributionControl({ compact: true }), 'bottom-left')

    const swapToFallback = (why: string) => {
      if (cancelled || swappedRef.current) return
      swappedRef.current = true
      setBasemapMode('fallback')
      setMapError(null)
      console.info(`[siltproof] offline basemap: ${why}`)
      map.setStyle(fallbackStyle(basemapRef.current))
    }

    // Set once the style document itself has loaded; tiles may still be
    // loading long after that (see fallBackOnError).
    let styleLoaded = false
    map.once('style.load', () => {
      styleLoaded = true
    })

    map.on('error', (event: ErrorEvent) => {
      const text = event.error?.message ?? 'map error'
      if (!swappedRef.current && startMode === 'location') {
        const status = (event.error as { status?: number } | undefined)?.status
        if (fallBackOnError(styleLoaded, status)) {
          swapToFallback(`Amazon Location style failed (${text})`)
        } else {
          console.warn(`[siltproof] basemap: ${text}`)
        }
        return
      }
      setMapError(text)
    })

    const loading = Promise.all([
      loadBasemap(),
      fetch(DRAINS_URL).then((response) => response.json()),
      fetch(DUMPSITE_URL).then((response) => response.json()),
    ])
      .then(([basemap, drains, dumpsite]: [unknown, GeoJSON.FeatureCollection, GeoJSON.FeatureCollection]) => {
        if (cancelled) return
        basemapRef.current = basemap
        const centroids: Record<string, LngLat> = {}
        for (const feature of drains.features) {
          const id = String(feature.properties?.drainId)
          centroids[id] = centroidOf(feature.geometry)
        }
        const dumpFeature = dumpsite.features[0]
        const dump = (dumpFeature?.properties?.center as LngLat | undefined) ??
          (dumpFeature ? centroidOf(dumpFeature.geometry) : center)
        geoRef.current = { drains, dumpsite, centroids, dump }
        setAttribution(basemapAttribution(basemap))

        if (swappedRef.current || startMode === 'fallback') {
          const source = map.getSource('basemap') as GeoJSONSource | undefined
          if (source && basemap) source.setData(basemap as never)
        }

        const ids = Object.keys(centroids)
        const lons = ids.map((id) => centroids[id][0])
        const lats = ids.map((id) => centroids[id][1])
        // A deep link into a case file frames itself; only the overview fits the ward.
        if (modeRef.current === 'overview') map.fitBounds(
          [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
          { padding: { top: 90, bottom: 90, left: 90, right: 420 }, duration: 0 },
        )
        setGeoReady(true)
        addDataLayers()
      })
      .catch((cause: unknown) =>
        setMapError(
          'Could not load the drain geometry. Run data/osm_drains.py and copy ' +
            `drains.geojson into frontend/public/data. (${String(cause)})`,
        ),
      )

    /**
     * Add the drains, dump site and case layers. Runs once the style is
     * ready in either basemap mode, and again after a style swap, because
     * setStyle throws every source and layer away.
     */
    let adding = false
    const addDataLayers = () => {
      // addSource fires styledata synchronously, which would re-enter here.
      if (adding || cancelled || !map.isStyleLoaded() || !geoRef.current) return
      if (map.getSource('drains') || map.getSource('dumpsite')) return
      adding = true
      try {
        addLayers()
      } finally {
        adding = false
      }
      setLayersReady((count) => count + 1)
    }

    const addLayers = () => {
      const { drains, dumpsite } = geoRef.current!

      // On the real basemap, bring land and water onto the survey palette.
      if (!swappedRef.current && isAmazonLocationUrl(startUrl)) {
        for (const [id, property, value] of surveyTint(map.getStyle().layers as never)) {
          try {
            map.setPaintProperty(id, property as never, value as never)
          } catch {
            // A layer the tint misjudged keeps the provider's colour.
          }
        }
      }

      if (!map.hasImage('hatch')) map.addImage('hatch', hatchImage())
      map.addSource('dumpsite', { type: 'geojson', data: dumpsite })
      map.addLayer({ id: 'dumpsite-fill', type: 'fill', source: 'dumpsite', paint: { 'fill-pattern': 'hatch' } })
      map.addLayer({ id: 'dumpsite-outline', type: 'line', source: 'dumpsite', paint: { 'line-color': INK, 'line-width': 1.6 } })

      map.addSource('drains', { type: 'geojson', data: drains, promoteId: 'drainId' })
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
          'fill-opacity': ['case', ['boolean', ['feature-state', 'dim'], false], 0.12, 0.5],
          'fill-opacity-transition': { duration: 300, delay: 0 },
        },
      })
      map.addLayer({
        id: 'drains-outline',
        type: 'line',
        source: 'drains',
        paint: {
          'line-color': INK,
          'line-width': 1,
          'line-opacity': ['case', ['boolean', ['feature-state', 'dim'], false], 0.25, 1],
        },
      })

      for (const id of ['claimed', 'others', 'actual', 'dim', 'stop']) {
        map.addSource(id, { type: 'geojson', data: EMPTY })
      }
      map.addLayer({ id: 'claimed', type: 'line', source: 'claimed', paint: { 'line-color': INK, 'line-width': 2.5, 'line-dasharray': [3, 2] } })
      map.addLayer({ id: 'others', type: 'line', source: 'others', paint: { 'line-color': COLOURS.RED, 'line-width': 1.4, 'line-opacity': 0.45 } })
      map.addLayer({ id: 'actual-casing', type: 'line', source: 'actual', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': PAPER, 'line-width': 9 } })
      map.addLayer({ id: 'actual', type: 'line', source: 'actual', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': COLOURS.RED, 'line-width': 4.5 } })
      map.addLayer({ id: 'dim', type: 'line', source: 'dim', paint: { 'line-color': INK, 'line-width': 1.6 } })
      map.addLayer({ id: 'stop', type: 'circle', source: 'stop', paint: { 'circle-radius': 8, 'circle-color': COLOURS.RED, 'circle-stroke-color': PAPER, 'circle-stroke-width': 3 } })
    }

    map.on('load', addDataLayers)
    map.on('styledata', addDataLayers)

    map.on('moveend', () => fitLabelsRef.current())

    let frame = 0
    map.on('move', () => {
      if (frame) return
      frame = requestAnimationFrame(() => {
        frame = 0
        setCamera((count) => count + 1)
      })
    })

    const timeout = window.setTimeout(() => {
      if (!cancelled && !styleLoaded) swapToFallback('Amazon Location style did not load in time')
    }, STYLE_TIMEOUT_MS)

    return () => {
      cancelled = true
      window.clearTimeout(timeout)
      cancelAnimationFrame(frame)
      void loading
      mapRef.current = null
      map.remove()
    }
  }, [])

  // ------------------------------------------------------------ the rings
  useEffect(() => {
    const map = mapRef.current
    const geo = geoRef.current
    if (!map || !geoReady || !geo) return
    const made = ringsRef.current
    for (const [id, at] of Object.entries(geo.centroids)) {
      const el = document.createElement('div')
      el.className = 'ring-marker'
      el.dataset.drain = id
      el.tabIndex = 0
      el.setAttribute('role', 'button')
      el.addEventListener('click', (event) => {
        event.stopPropagation()
        callbacks.current.onSelect(id)
      })
      el.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          callbacks.current.onSelect(id)
        }
      })
      el.addEventListener('mouseenter', () => callbacks.current.onHover(id))
      el.addEventListener('mouseleave', () => callbacks.current.onHover(null))
      el.addEventListener('focus', () => callbacks.current.onHover(id))
      el.addEventListener('blur', () => callbacks.current.onHover(null))
      const tag = document.createElement('div')
      tag.className = 'maptag-marker'
      made[id] = {
        el,
        tag,
        markers: [
          new Marker({ element: el }).setLngLat(at).addTo(map),
          new Marker({ element: tag, anchor: 'left', offset: [26, 0] }).setLngLat(at).addTo(map),
        ],
      }
    }
    return () => {
      for (const ring of Object.values(made)) ring.markers.forEach((marker) => marker.remove())
      ringsRef.current = {}
    }
  }, [geoReady])

  const caseId = caseDrain?.drainId ?? null

  useEffect(() => {
    const byId = Object.fromEntries(rows.map((row) => [row.drainId, row]))
    for (const [id, ring] of Object.entries(ringsRef.current)) {
      const row = byId[id]
      if (!row) continue
      const shown = verified && (revealed === null || revealed.has(id))
      // MapLibre owns the marker element's opacity, so toggle the child.
      ring.el.innerHTML = ringSvg(row, shown)
      const svg = ring.el.firstElementChild as HTMLElement | null
      const hidden = mode !== 'overview' && id !== caseId
      if (svg) {
        svg.classList.toggle('is-hover', hoverId === id)
        svg.style.visibility = hidden ? 'hidden' : 'visible'
      }
      ring.el.tabIndex = hidden ? -1 : 0
      ring.el.setAttribute('aria-hidden', hidden ? 'true' : 'false')
      const verdictText = !shown
        ? 'not checked yet'
        : row.heldTonnes
          ? `held ${rupees(row.heldTonnes * rate)}`
          : row.reviewTonnes
            ? `${grouped(row.reviewTonnes)} t for review`
            : 'every check passed'
      ring.el.setAttribute('aria-label', `Drain ${id}, ${grouped(row.claimedTonnes)} t billed, ${verdictText}`)
      const showTag = shown && row.heldTonnes > 0 && mode === 'overview'
      ring.tag.innerHTML = showTag
        ? `<div class="maptag">Held <span class="mono">₹${lakh(row.heldTonnes * rate)} L</span></div>`
        : ''
    }

    const map = mapRef.current
    if (!map || !layersReady || !map.getSource('drains')) return
    for (const row of rows) {
      const shown = verified && (revealed === null || revealed.has(row.drainId))
      map.setFeatureState(
        { source: 'drains', id: row.drainId },
        {
          verdict: shown ? (row.verdict ?? 'PENDING') : 'PENDING',
          dim: mode !== 'overview' && row.drainId !== caseId,
        },
      )
    }
  }, [rows, revealed, verified, hoverId, mode, caseId, rate, layersReady, geoReady])

  // -------------------------------------------------------- case labels
  const label = useCallback((key: string, at: LngLat, html: string, cls: string, anchor: Anchor, offset: [number, number]) => {
    const map = mapRef.current
    if (!map) return
    labelsRef.current[key]?.marker.remove()
    const el = document.createElement('div')
    el.className = `maplabel ${cls}`
    el.innerHTML = html
    const marker = new Marker({ element: el, anchor, offset }).setLngLat(at).addTo(map)
    labelsRef.current[key] = { marker, at, html, cls, anchor, offset }
  }, [])

  const clearLabels = useCallback(() => {
    for (const entry of Object.values(labelsRef.current)) entry.marker.remove()
    labelsRef.current = {}
  }, [])

  /**
   * Keep the case labels inside the map frame and off each other: a label
   * that runs past the side it hangs towards moves to the other side of its
   * point, anything still outside is nudged in, and a label that lands on an
   * earlier one drops below it. Narrow frames (mobile) need this; on wide
   * ones every label already fits and nothing moves.
   */
  const fitLabels = useCallback(() => {
    const map = mapRef.current
    if (!map) return
    const box = map.getContainer().getBoundingClientRect()
    if (!box.width) return
    const placed: DOMRect[] = []
    for (const key of Object.keys(labelsRef.current)) {
      let entry = labelsRef.current[key]
      entry.marker.setOffset(entry.offset)
      let r = entry.marker.getElement().getBoundingClientRect()
      if (!r.width) continue
      const pastLeft = r.left < box.left + LABEL_INSET
      const pastRight = r.right > box.right - LABEL_INSET
      if ((pastLeft && entry.anchor === 'right') || (pastRight && entry.anchor === 'left')) {
        label(key, entry.at, entry.html, entry.cls, entry.anchor === 'right' ? 'left' : 'right', [-entry.offset[0], entry.offset[1]])
        entry = labelsRef.current[key]
        r = entry.marker.getElement().getBoundingClientRect()
      }
      let dx = 0
      if (r.left < box.left + LABEL_INSET) dx = box.left + LABEL_INSET - r.left
      else if (r.right > box.right - LABEL_INSET) dx = Math.max(box.left + LABEL_INSET - r.left, box.right - LABEL_INSET - r.right)
      let dy = 0
      for (const other of placed) {
        const overlaps =
          r.left + dx < other.right && r.right + dx > other.left && r.top + dy < other.bottom && r.bottom + dy > other.top
        if (overlaps) dy = other.bottom + 4 - r.top
      }
      if (dx || dy) {
        entry.marker.setOffset([entry.offset[0] + dx, entry.offset[1] + dy])
        r = entry.marker.getElement().getBoundingClientRect()
      }
      placed.push(r)
    }
  }, [label])

  useEffect(() => {
    fitLabelsRef.current = fitLabels
  })

  const setSource = (id: string, data: GeoJSON.FeatureCollection | GeoJSON.Feature) => {
    const source = mapRef.current?.getSource(id) as GeoJSONSource | undefined
    source?.setData(data)
  }

  /** Draw the finished case map: routes, the stop, the measured shortfall, labels. */
  const showCase = useCallback(
    (drain: Drain, f: CaseFacts) => {
      const geo = geoRef.current
      if (!geo || !mapRef.current?.getSource('claimed')) return
      const trip = f.trip
      setSource('claimed', drain.claimedRoute ? line(drain.claimedRoute) : EMPTY)
      setSource('actual', trip && trip.actualRoute.length > 1 ? line(trip.actualRoute) : EMPTY)
      setSource('others', {
        type: 'FeatureCollection',
        features: drain.trips
          .filter((other) => other !== trip && other.actualRoute.length > 1)
          .map((other) => line(other.actualRoute)),
      })
      const short = !f.reachedDump && f.stop
      setSource('stop', short ? { type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: f.stop! } } : EMPTY)
      setSource('dim', short ? dimensionLine(f.stop!, f.dump, 1) : EMPTY)

      clearLabels()
      const at = geo.centroids[drain.drainId]
      if (at) label('drain', at, `Drain ${drain.drainId}<span class="sub">loads leave from here</span>`, '', 'right', [-30, 0])
      if (short && f.stop && trip) {
        const mid: LngLat = [(f.stop[0] + f.dump[0]) / 2, (f.stop[1] + f.dump[1]) / 2]
        label('dim', mid, `${((f.shortOfDumpM ?? 0) / 1000).toFixed(1)} km short`, 'dim', 'center', [0, 0])
        const times = [
          trip.arrivalTime ? `stops ${trip.arrivalTime.slice(11, 16)}` : null,
          f.r8?.comparedKind === 'lastFix' && f.r8.compared ? `last fix ${f.r8.compared}` : null,
        ].filter(Boolean).join(', ')
        label(
          'stop',
          f.stop,
          `${f.allStopTogether ? 'Every trace stops here' : `Trip ${trip.tripNo}'s trace stops here`}<span class="sub">trip ${trip.tripNo}${times ? `: ${times}` : ''}</span>`,
          'held',
          'right',
          [-16, 0],
        )
        label('dump', f.dump, `Approved dump site<span class="sub">${f.allStopTogether ? 'no trace enters it' : `trip ${trip.tripNo} never enters it`}</span>`, '', 'bottom', [0, -22])
      } else {
        label('dump', f.dump, 'Approved dump site', '', 'bottom', [0, -22])
      }
      requestAnimationFrame(() => fitLabelsRef.current())
    },
    [clearLabels, label],
  )

  const clearCase = useCallback(() => {
    for (const id of ['claimed', 'others', 'actual', 'dim', 'stop']) setSource(id, EMPTY)
    clearLabels()
  }, [clearLabels])

  const caseCamera = useCallback((drain: Drain, f: CaseFacts) => {
    const map = mapRef.current
    if (!map) return null
    const points: LngLat[] = [
      ...(drain.claimedRoute ?? []),
      ...(f.trip?.actualRoute ?? []),
      f.dump,
    ]
    if (points.length < 2) return null
    const width = map.getContainer().clientWidth
    const camera = map.cameraForBounds(boundsOfPoints(points) as LngLatBoundsLike, {
      padding: {
        top: 80,
        bottom: 40,
        left: Math.min(330, Math.round(width * 0.3)),
        right: 110,
      },
      bearing: 0,
    })
    return camera ?? null
  }, [])

  // Enter and leave the case file.
  const wasCase = useRef(false)
  useEffect(() => {
    const map = mapRef.current
    if (!map || !layersReady) return
    // A change of drain, trip or mode ends any replay still drawing.
    runRef.current += 1
    setReplay(null)
    if (mode === 'case' && caseDrain && facts) {
      showCase(caseDrain, facts)
      map.easeTo({ pitch: 0, bearing: 0, duration: reducedMotion ? 0 : 900 })
    } else if (mode === 'overview') {
      clearCase()
      const geo = geoRef.current
      // The frame has just been resized back to the overview plate; make the
      // map measure it before fitting, or the fit uses Exhibit A's size.
      map.resize()
      if (geo && (map.getPitch() > 0 || Math.abs(map.getBearing()) > 0.5 || wasCase.current)) {
        const ids = Object.keys(geo.centroids)
        const lons = ids.map((id) => geo.centroids[id][0])
        const lats = ids.map((id) => geo.centroids[id][1])
        map.fitBounds(
          [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
          { padding: { top: 90, bottom: 90, left: 90, right: 420 }, pitch: 0, bearing: 0, duration: reducedMotion ? 0 : 700 },
        )
      }
    }
    wasCase.current = mode !== 'overview'
  }, [mode, caseDrain, facts, layersReady, showCase, clearCase, reducedMotion])

  // In the case file, keep the haul framed whenever the frame changes size.
  useEffect(() => {
    const map = mapRef.current
    const container = containerRef.current
    if (!map || !container || mode !== 'case' || !caseDrain || !facts) return
    let timer = 0
    let tries = 0
    const fit = (duration: number) => {
      // Measure the frame first: MapLibre's own resize may not have run yet.
      map.resize()
      const camera = caseCamera(caseDrain, facts)
      if (camera) map.easeTo({ ...camera, pitch: 0, bearing: 0, duration: reducedMotion ? 0 : duration })
      else if (tries++ < 10) timer = window.setTimeout(() => fit(duration), 150)
    }
    const observer = new ResizeObserver(() => {
      window.clearTimeout(timer)
      timer = window.setTimeout(() => fit(350), 120)
    })
    observer.observe(container)
    timer = window.setTimeout(() => fit(600), 120)
    return () => {
      observer.disconnect()
      window.clearTimeout(timer)
    }
  }, [mode, caseDrain, facts, caseCamera, reducedMotion, geoReady, layersReady])

  /**
   * Draw the trip's recorded trace from its first fix to its last, then show
   * how it ends: inside the dump site, or stopped short with the measured
   * gap. Only the recorded points are drawn; nothing is added past the last
   * fix. A newer run (another replay, or a change of trip) cancels it.
   */
  const playTrace = useCallback(
    async (drain: Drain, f: CaseFacts, run: number, hold: number) => {
      const trip = f.trip
      if (!trip) return
      const live = () => run === runRef.current
      const route = trip.actualRoute
      const total = trip.actualRouteDistanceM / 1000
      const left = trip.startTime?.slice(11, 16) ?? '—'
      const name = `Trip ${trip.tripNo}, ${trip.vehicleNo}`
      await tween(
        REPLAY_MS,
        (p) => {
          if (!live()) return
          setSource('actual', line(sliceLine(route, p)))
          setReplay({ trip: name, left, km: (total * p).toFixed(1), done: null })
        },
        ease.inOut,
        false,
      )
      if (!live()) return

      if (!f.reachedDump && f.stop) {
        setSource('stop', { type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: f.stop } })
        if (f.allStopTogether) {
          setSource('others', {
            type: 'FeatureCollection',
            features: drain.trips.filter((other) => other !== trip && other.actualRoute.length > 1).map((other) => line(other.actualRoute)),
          })
        }
        setReplay({ trip: name, left, km: total.toFixed(1), done: `stops ${((f.shortOfDumpM ?? 0) / 1000).toFixed(1)} km short of the dump site` })
        await tween(DIMENSION_MS, (p) => { if (live()) setSource('dim', dimensionLine(f.stop!, f.dump, p)) }, ease.out, false)
        if (!live()) return
        const mid: LngLat = [(f.stop[0] + f.dump[0]) / 2, (f.stop[1] + f.dump[1]) / 2]
        label('dim', mid, `${((f.shortOfDumpM ?? 0) / 1000).toFixed(1)} km short`, 'dim', 'center', [0, 0])
        fitLabelsRef.current()
      } else {
        const arrived = trip.arrivalTime?.slice(11, 16)
        setReplay({ trip: name, left, km: total.toFixed(1), done: arrived ? `enters the dump site at ${arrived}` : 'enters the dump site' })
      }
      await wait(hold, false)
      if (live()) setReplay(null)
    },
    [label],
  )

  // ------------------------------------------------------------- handle
  useImperativeHandle(
    ref,
    () => ({
      async sweep(onPass, ms = 3400) {
        const map = mapRef.current
        const geo = geoRef.current
        const ids = Object.keys(geo?.centroids ?? {})
        if (!map || !geo || reducedMotion) {
          onPass(ids.length ? ids : rows.map((row) => row.drainId), 1)
          return
        }
        const height = map.getContainer().clientHeight
        const order = ids
          .map((id) => ({ id, y: map.project(geo.centroids[id]).y }))
          .sort((a, b) => a.y - b.y)
        const passed = new Set<string>()
        const sweep = sweepRef.current
        if (sweep) sweep.style.opacity = '1'
        await tween(
          ms,
          (p) => {
            const y = -10 + p * (height + 20)
            if (sweep) sweep.style.transform = `translateY(${y}px)`
            const fresh = order.filter((o) => o.y < y && !passed.has(o.id)).map((o) => o.id)
            fresh.forEach((id) => passed.add(id))
            if (fresh.length || p >= 1) onPass(fresh, p)
          },
          ease.linear,
          false,
        )
        if (sweep) sweep.style.opacity = '0'
        // Anything off screen is passed at the end.
        onPass(ids.filter((id) => !passed.has(id)), 1)
      },

      async fly(drain, f) {
        const map = mapRef.current
        const geo = geoRef.current
        const trip = f.trip
        if (!map || !geo || !trip || trip.actualRoute.length < 2 || reducedMotion) return
        const route = trip.actualRoute
        const start = geo.centroids[drain.drainId] ?? route[0]
        const brg = bearingTo(start, f.dump)

        // The claimed haul and the destination stay in view the whole way.
        clearCase()
        setSource('claimed', drain.claimedRoute ? line(drain.claimedRoute) : EMPTY)
        setSource('actual', line([route[0], route[0]]))
        label('dump', f.dump, 'Approved dump site', '', 'bottom', [0, -22])
        label('drain', start, `Drain ${drain.drainId}<span class="sub">loads leave from here</span>`, '', 'right', [-30, 0])

        const points: LngLat[] = [...(drain.claimedRoute ?? []), ...route, f.dump]
        const framed = map.cameraForBounds(boundsOfPoints(points) as LngLatBoundsLike, {
          padding: { top: 80, bottom: 150, left: 90, right: 90 },
          bearing: brg,
        })
        // Tilted, the near end grows and the far end shrinks, so look at a
        // point a little nearer the drain than the middle of the haul.
        const look: LngLat = [start[0] + (f.dump[0] - start[0]) * 0.45, start[1] + (f.dump[1] - start[1]) * 0.45]
        map.flyTo({
          center: look,
          zoom: (framed?.zoom ?? 14) + 0.1,
          pitch: 52,
          bearing: brg,
          duration: TILT_MS,
          curve: 1.2,
          essential: true,
        })
        await wait(TILT_MS + 50, false)

        await playTrace(drain, f, ++runRef.current, HOLD_MS)
      },

      async replay(drain, f) {
        const map = mapRef.current
        const geo = geoRef.current
        const trip = f.trip
        if (!map || !geo || !trip || trip.actualRoute.length < 2 || reducedMotion) return
        const run = ++runRef.current
        const route = trip.actualRoute
        // The opening flight's drawing, in the case frame as it stands: the
        // claimed haul and the destination stay, the recorded trace is redrawn.
        clearCase()
        setSource('claimed', drain.claimedRoute ? line(drain.claimedRoute) : EMPTY)
        setSource('actual', line([route[0], route[0]]))
        label('dump', f.dump, 'Approved dump site', '', 'bottom', [0, -22])
        label('drain', geo.centroids[drain.drainId] ?? route[0], `Drain ${drain.drainId}<span class="sub">loads leave from here</span>`, '', 'right', [-30, 0])
        fitLabelsRef.current()
        await playTrace(drain, f, run, REPLAY_HOLD_MS)
        if (run === runRef.current) showCase(drain, f)
      },
    }),
    [reducedMotion, rows, clearCase, label, showCase, playTrace],
  )

  // ---------------------------------------------------------- furniture
  // Ticks, scale, north arrow, off-map pointer and tooltip follow the camera.
  // They are worked out after each move, outside render, and kept in state.
  const furnitureInputs = useRef({ mode, hoverId, rows })
  useEffect(() => {
    furnitureInputs.current = { mode, hoverId, rows }
  })

  const computeFurniture = useCallback((): Furniture => {
    const map = mapRef.current
    const geo = geoRef.current
    const { mode: m, hoverId: hover, rows: currentRows } = furnitureInputs.current
    const out: Furniture = { ticks: [], scale: { width: 80, text: '' }, bearing: 0, offmap: null, tip: null }
    if (!map || !geo) return out
    const w = map.getContainer().clientWidth
    const h = map.getContainer().clientHeight
    out.bearing = map.getBearing()
    const flat = map.getPitch() < 3 && Math.abs(out.bearing) < 1

    const y = h * 0.66
    const a = map.unproject([0, y])
    const b = map.unproject([100, y])
    const per100 = metres([a.lng, a.lat], [b.lng, b.lat])
    if (per100 > 0) {
      const nice = [100, 200, 250, 500, 1000, 2000, 5000].find((n) => (n / per100) * 100 > 60) ?? 5000
      out.scale = { width: (nice / per100) * 100, text: nice >= 1000 ? `${nice / 1000} km` : `${nice} m` }
    }

    if (flat && m === 'overview') {
      const bounds = map.getBounds()
      const step = bounds.getEast() - bounds.getWest() > 0.08 ? 0.02 : 0.01
      for (let x = Math.ceil(bounds.getWest() / step) * step; x < bounds.getEast(); x += step) {
        const p = map.project([x, bounds.getNorth()])
        if (p.x > 60 && p.x < w - 60) out.ticks.push({ key: `x${x.toFixed(3)}`, side: 'top', style: { left: p.x }, text: `${x.toFixed(2)}° E` })
      }
      for (let yy = Math.ceil(bounds.getSouth() / step) * step; yy < bounds.getNorth(); yy += step) {
        const p = map.project([bounds.getWest(), yy])
        if (p.y > 60 && p.y < h - 60) out.ticks.push({ key: `y${yy.toFixed(3)}`, side: 'left', style: { top: p.y }, text: `${yy.toFixed(2)}° N` })
      }
    }

    if (m === 'overview') {
      const p = map.project(geo.dump)
      if (!(p.x > 0 && p.x < w && p.y > 0 && p.y < h)) {
        const cx = w / 2
        const cy = h / 2
        const dx = p.x - cx
        const dy = p.y - cy
        const k = Math.min((w / 2 - 190) / Math.abs(dx || 1), (h / 2 - 40) / Math.abs(dy || 1))
        const ids = Object.keys(geo.centroids)
        const ward: LngLat = [
          ids.reduce((sum, id) => sum + geo.centroids[id][0], 0) / ids.length,
          ids.reduce((sum, id) => sum + geo.centroids[id][1], 0) / ids.length,
        ]
        out.offmap = {
          left: Math.max(8, Math.min(w - 330, cx + dx * k - 150)),
          top: Math.max(8, Math.min(h - 44, cy + dy * k - 16)),
          angle: (Math.atan2(dy, dx) * 180) / Math.PI,
          km: (metres(ward, geo.dump) / 1000).toFixed(1),
        }
      }
      const row = hover ? currentRows.find((entry) => entry.drainId === hover) : null
      const at = hover ? geo.centroids[hover] : null
      if (row && at) {
        const pt = map.project(at)
        // Flip the tooltip to the left of the ring near the right edge.
        out.tip = { x: pt.x, y: Math.max(70, Math.min(h - 70, pt.y)), row, left: pt.x > w - 340 }
      }
    }
    return out
  }, [])

  const [furniture, setFurniture] = useState<Furniture>(EMPTY_FURNITURE)
  const frameRef = useRef(0)
  const refreshFurniture = useCallback(() => {
    if (frameRef.current) return
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = 0
      setFurniture(computeFurniture())
    })
  }, [computeFurniture])

  useEffect(() => {
    refreshFurniture()
  }, [camera, mode, hoverId, rows, geoReady, layersReady, refreshFurniture])

  useEffect(
    () => () => {
      cancelAnimationFrame(frameRef.current)
      frameRef.current = 0
    },
    [],
  )

  const { ticks, scale, bearing, offmap, tip } = furniture
  const tipShown = tip && verified && (revealed === null || revealed.has(tip.row.drainId))
  const tonnesRange = rows.length
    ? `${Math.min(...rows.map((row) => row.claimedTonnes))} t to ${Math.max(...rows.map((row) => row.claimedTonnes))} t`
    : ''

  return (
    <div className={`map-root mode-${mode}`}>
      <div ref={containerRef} className="map" data-testid="map" role="img" aria-label="Survey map of the ward's drains" />
      <div className="neat" aria-hidden="true" />
      <div className="ticks" aria-hidden="true">
        {ticks.map((tick) => (
          <span key={tick.key} className={`tick ${tick.side} mono`} style={tick.style}>
            {tick.text}
          </span>
        ))}
      </div>
      <div ref={sweepRef} className="sweep" aria-hidden="true">
        <span>Checking the evidence</span>
      </div>
      <div className="north" aria-hidden="true" style={{ transform: `rotate(${-bearing}deg)` }}>
        <svg width="26" height="38" viewBox="0 0 28 40">
          <path d="M14 2 L24 34 L14 27 L4 34 Z" fill="none" stroke={INK} strokeWidth="1.5" />
          <path d="M14 2 L14 27 L4 34 Z" fill={INK} />
        </svg>
        <div>N</div>
      </div>

      <div className={`titleblock ${mode === 'flying' ? 'faded' : ''}`}>
        {mode === 'overview' ? (
          <>
            <h3>{verified ? 'Drains by what the evidence supports' : 'Drains on this bill, not yet checked'}</h3>
            <div className="legend">
              <svg width="36" height="36" aria-hidden="true">
                <circle cx="18" cy="18" r="13" fill="none" stroke={COLOURS.PENDING} strokeWidth="5" strokeDasharray="3 2" />
              </svg>
              <span>Ring area is tonnes billed, {tonnesRange}.</span>
              {verified && (
                <>
                  <svg width="36" height="36" aria-hidden="true">
                    <circle cx="18" cy="18" r="13" fill="none" stroke={COLOURS.GREEN} strokeWidth="5" strokeDasharray="50 100" transform="rotate(-90 18 18)" />
                    <circle cx="18" cy="18" r="13" fill="none" stroke={COLOURS.RED} strokeWidth="5" strokeDasharray="32 100" strokeDashoffset="-50" transform="rotate(-90 18 18)" />
                  </svg>
                  <span>
                    Ring split: <b className="k-verified">verified</b>, <b className="k-review">for review</b>,{' '}
                    <b className="k-held">held</b>.
                  </span>
                </>
              )}
            </div>
          </>
        ) : (
          caseDrain && (
            <>
              <h3>Drain {caseDrain.drainId} to the dump site</h3>
              <div className="legend">
                <svg width="36" height="10" aria-hidden="true"><line x1="0" y1="5" x2="36" y2="5" stroke={INK} strokeWidth="2.5" strokeDasharray="6 4" /></svg>
                <span>
                  Haul the bill implies
                  {facts?.claimedKm ? <>, <span className="mono">{facts.claimedKm.toFixed(1)} km</span></> : null}
                </span>
                <svg width="36" height="10" aria-hidden="true"><line x1="0" y1="5" x2="36" y2="5" stroke={COLOURS.RED} strokeWidth="4.5" /></svg>
                <span>GPS trace, trip {facts?.trip?.tripNo}</span>
                {caseDrain.trips.length > 1 && (
                  <>
                    <svg width="36" height="10" aria-hidden="true"><line x1="0" y1="5" x2="36" y2="5" stroke={COLOURS.RED} strokeWidth="1.4" opacity=".55" /></svg>
                    <span>The other {caseDrain.trips.length - 1} traces</span>
                  </>
                )}
                <svg width="36" height="20" aria-hidden="true">
                  <defs>
                    <pattern id="hatch-key" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                      <line x1="0" y1="0" x2="0" y2="6" stroke={INK} strokeWidth="1.5" />
                    </pattern>
                  </defs>
                  <rect x="5" y="2" width="26" height="16" fill="url(#hatch-key)" stroke={INK} />
                </svg>
                <span>Approved dump site geofence</span>
              </div>
            </>
          )
        )}
        <div className="scale">
          <i style={{ width: scale.width }} />
          <span className="num">{scale.text}</span>
        </div>
      </div>

      {offmap && (
        <div className="offmap" style={{ left: offmap.left, top: offmap.top }}>
          <svg width="18" height="12" viewBox="0 0 18 12" aria-hidden="true" style={{ transform: `rotate(${offmap.angle}deg)` }}>
            <path d="M0 6h14M10 1l5 5-5 5" fill="none" stroke={INK} strokeWidth="1.8" />
          </svg>
          Approved dump site, <span className="mono">{offmap.km} km</span> from the ward
        </div>
      )}

      {tip && (
        <div className={`maptip ${tip.left ? 'flip' : ''}`} style={{ left: tip.x, top: tip.y }} role="tooltip">
          <b>
            Drain {tip.row.drainId}, {tip.row.name}
          </b>
          <span className="num">{grouped(tip.row.claimedTonnes)} t</span> billed over {tip.row.tripCount} trips
          {tipShown && tip.row.heldTonnes > 0 && <div className="money held">{rupees(tip.row.heldTonnes * rate)} held</div>}
          {tipShown && tip.row.reviewTonnes > 0 && (
            <div className="money review">{grouped(tip.row.reviewTonnes)} t for your review</div>
          )}
          {tipShown && tip.row.verdict !== 'GREEN' && reasons[tip.row.drainId] && <div className="tip-why">{reasons[tip.row.drainId]}</div>}
          {tipShown && tip.row.verdict === 'GREEN' && <div className="ok">Every check passed</div>}
          {!tipShown && <div className="tip-muted">Not checked yet</div>}
          <div className="tip-open">Click to open the evidence</div>
        </div>
      )}

      {replay && (
        <div className="replay" aria-live="polite">
          <span>{replay.trip}</span>
          <span>
            leaves drain {caseDrain?.drainId} at <b className="mono">{replay.left}</b>
          </span>
          <span>
            driven <b className="mono">{replay.km} km</b>
          </span>
          {replay.done && <span className="held">{replay.done}</span>}
        </div>
      )}

      {basemapMode === 'fallback' && (
        <div
          className="map-mode"
          title={
            locationConfigured
              ? 'Amazon Location did not load, so the bundled offline style is being used.'
              : 'No Amazon Location API key configured, so the bundled offline style is being used.'
          }
        >
          Offline basemap{attribution ? `, ${attribution}` : ''}
        </div>
      )}

      {mapError && <div className="map-note">{mapError}</div>}
    </div>
  )
}

export default MapView
