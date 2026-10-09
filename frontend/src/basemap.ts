import type { StyleSpecification } from '@maplibre/maplibre-gl-style-spec'

/**
 * Which basemap the map draws under the drains.
 *
 * `location` is the real thing: Amazon Location's Standard style, which is
 * what ships and what the demo uses. `fallback` is a style built here from a
 * GeoJSON file served with the app, for when there is no API key yet or the
 * Amazon Location style will not load. The fallback makes no network request
 * of its own: no tiles, no glyphs, no sprite, no third-party anything.
 */
export type BasemapMode = 'location' | 'fallback'

export const region = (import.meta.env.VITE_AWS_REGION ?? '').trim()
const rawKey = (import.meta.env.VITE_LOCATION_API_KEY ?? '').trim()
// The .env.example placeholder counts as no key, so a copied example file
// never sends a request that is bound to be refused.
export const apiKey = rawKey === 'replace-me' ? '' : rawKey

/**
 * Amazon Location Maps v2 style and colour scheme. Monochrome Light is the
 * quiet base the survey sheet is designed on; Standard, Hybrid and Satellite
 * are the others the service offers.
 */
export const mapStyle = (import.meta.env.VITE_LOCATION_MAP_STYLE ?? '').trim() || 'Monochrome'
export const colourScheme = (import.meta.env.VITE_LOCATION_COLOR_SCHEME ?? '').trim() || 'Light'

/** The Amazon Location style can only be used when both halves are present. */
export const locationConfigured = Boolean(region && apiKey)

/**
 * ?style=<url> overrides the basemap style. A dev aid: it is how the
 * fall-back-on-failure path gets exercised without an API key (point it at a
 * URL that will not load) and how an alternative style can be tried without
 * a rebuild.
 */
export function styleOverride(): string | null {
  if (typeof window === 'undefined') return null
  return new URLSearchParams(window.location.search).get('style')
}

export function locationStyleUrl(
  theRegion = region,
  theKey = apiKey,
  theStyle = mapStyle,
  theScheme = colourScheme,
): string {
  return (
    `https://maps.geo.${theRegion}.amazonaws.com/v2/styles/${encodeURIComponent(theStyle)}/descriptor` +
    `?key=${encodeURIComponent(theKey)}&color-scheme=${encodeURIComponent(theScheme)}`
  )
}

/** True for a style URL served by Amazon Location. */
export function isAmazonLocationUrl(url: string | null) {
  return Boolean(url && /^https:\/\/maps\.geo\.[a-z0-9-]+\.amazonaws\.com\//.test(url))
}

type StyleLayer = { id: string; type: string; 'source-layer'?: string }

/**
 * Paint changes that bring a loaded Amazon Location style onto the survey
 * sheet palette: the land becomes limestone and the water grey-green, so
 * the drains, traces and stamps read the same as on the offline map. Only
 * background and water layers are touched; roads, labels and everything
 * else keep the provider's styling. A pure function of the style's layers,
 * so it is tested without a network.
 */
export function surveyTint(layers: StyleLayer[]): Array<[string, string, string]> {
  const changes: Array<[string, string, string]> = []
  for (const layer of layers) {
    const name = `${layer.id} ${layer['source-layer'] ?? ''}`.toLowerCase()
    const water = /water|ocean|sea|river|lake|canal/.test(name)
    if (layer.type === 'background') changes.push([layer.id, 'background-color', PAPER])
    else if (water && layer.type === 'fill') changes.push([layer.id, 'fill-color', WATER])
    else if (water && layer.type === 'line') changes.push([layer.id, 'line-color', WATER])
  }
  return changes
}

/** The style to start with, or null when there is nothing but the fallback. */
export function startingStyleUrl(): string | null {
  const override = styleOverride()
  if (override) return override
  return locationConfigured ? locationStyleUrl() : null
}

export function initialMode(
  configured = locationConfigured || Boolean(styleOverride()),
): BasemapMode {
  return configured ? 'location' : 'fallback'
}

// The offline backdrop, written by data/osm_basemap.py. Same origin, shipped
// with the app; if it is missing the style simply has nothing in it.
export const BASEMAP_URL = '/data/basemap.geojson'

const EMPTY_COLLECTION = { type: 'FeatureCollection' as const, features: [] }

// The survey-sheet palette (design/DESIGN.md): limestone ground, grey-green
// water, roads a shade lighter than the ground so the data reads on top.
const PAPER = '#e3ded2'
const WATER = '#aec0c3'
const ROAD_MINOR = '#f4f1ea'
const ROAD_MAJOR = '#fbf8f1'
const ROAD_CASING = '#dad4c6'

/**
 * Build the offline style around whatever backdrop we have.
 *
 * The GeoJSON is inlined rather than referenced by URL, so the style object
 * itself contains no URLs at all - which is what `styleIsSelfContained` below
 * checks, and what the tests assert.
 */
export function fallbackStyle(basemap: unknown = null): StyleSpecification {
  const data = (basemap ?? EMPTY_COLLECTION) as never

  return {
    version: 8,
    name: 'SiltProof offline',
    // No glyphs and no sprite on purpose: both are URLs, and the fallback
    // draws no text, so it needs neither.
    sources: {
      basemap: { type: 'geojson', data },
    },
    layers: [
      {
        id: 'paper',
        type: 'background',
        paint: { 'background-color': PAPER },
      },
      {
        id: 'water-body',
        type: 'fill',
        source: 'basemap',
        filter: ['all', ['==', ['get', 'kind'], 'water'], ['==', ['geometry-type'], 'Polygon']],
        paint: { 'fill-color': WATER },
      },
      {
        id: 'water-line',
        type: 'line',
        source: 'basemap',
        filter: [
          'all',
          ['==', ['get', 'kind'], 'water'],
          ['==', ['geometry-type'], 'LineString'],
        ],
        paint: { 'line-color': WATER, 'line-width': 3 },
      },
      {
        id: 'road-casing',
        type: 'line',
        source: 'basemap',
        filter: ['==', ['get', 'kind'], 'road'],
        paint: {
          'line-color': ROAD_CASING,
          'line-width': [
            'interpolate', ['linear'], ['zoom'],
            11, ['case', ['==', ['get', 'class'], 'major'], 2.5, 1],
            16, ['case', ['==', ['get', 'class'], 'major'], 11, 6],
          ],
        },
      },
      {
        id: 'road-minor',
        type: 'line',
        source: 'basemap',
        filter: ['all', ['==', ['get', 'kind'], 'road'], ['==', ['get', 'class'], 'minor']],
        paint: {
          'line-color': ROAD_MINOR,
          'line-width': ['interpolate', ['linear'], ['zoom'], 11, 0.5, 16, 4.5],
        },
      },
      {
        id: 'road-major',
        type: 'line',
        source: 'basemap',
        filter: ['all', ['==', ['get', 'kind'], 'road'], ['==', ['get', 'class'], 'major']],
        paint: {
          'line-color': ROAD_MAJOR,
          'line-width': ['interpolate', ['linear'], ['zoom'], 11, 1.5, 16, 9],
        },
      },
    ],
  }
}

/** True when a style would not touch the network for anything. */
export function styleIsSelfContained(style: unknown): boolean {
  return !/https?:\/\//i.test(JSON.stringify(style))
}

/** Fetch the bundled backdrop. Returns null when there is not one. */
export async function loadBasemap(url = BASEMAP_URL): Promise<unknown | null> {
  try {
    const response = await fetch(url)
    if (!response.ok) return null
    return await response.json()
  } catch {
    // A missing backdrop is not an error: the style falls back to plain paper.
    return null
  }
}

/** What the backdrop file says about where its geometry came from. */
export function basemapAttribution(basemap: unknown): string | null {
  const properties = (basemap as { properties?: { attribution?: string } } | null)?.properties
  return properties?.attribution ?? null
}


// ----------------------------------------------------------------- drains
/** Verdict colours, shared by the map layers and the tests. */
export const COLOURS: Record<string, string> = {
  RED: '#8e2424',
  AMBER: '#a6731e',
  GREEN: '#39684f',
  PENDING: '#7d868b',
}

/** Paint colour for a drain, with anything unverified left grey. */
export function colourFor(verdict: string | null | undefined): string {
  return COLOURS[verdict ?? 'PENDING'] ?? COLOURS.PENDING
}

export type FeatureCollection = {
  features: Array<{ geometry: { coordinates: number[][][] } }>
}

/** The box containing every ring of every feature, or null if there are none. */
export function boundsOf(
  collections: FeatureCollection[],
): [[number, number], [number, number]] | null {
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
