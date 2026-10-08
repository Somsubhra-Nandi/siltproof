import { describe, expect, it, vi } from 'vitest'

import {
  basemapAttribution,
  startingStyleUrl,
  styleOverride,
  boundsOf,
  colourFor,
  COLOURS,
  fallbackStyle,
  initialMode,
  loadBasemap,
  locationStyleUrl,
  styleIsSelfContained,
} from '../basemap'

const SAMPLE_BASEMAP = {
  type: 'FeatureCollection',
  properties: { source: 'openstreetmap', attribution: '© OpenStreetMap contributors, ODbL' },
  features: [
    {
      type: 'Feature',
      properties: { kind: 'road', class: 'major' },
      geometry: { type: 'LineString', coordinates: [[72.87, 19.07], [72.88, 19.08]] },
    },
    {
      type: 'Feature',
      properties: { kind: 'water', class: 'body' },
      geometry: {
        type: 'Polygon',
        coordinates: [[[72.87, 19.07], [72.88, 19.07], [72.88, 19.08], [72.87, 19.07]]],
      },
    },
  ],
}

// ------------------------------------------------------- choosing a style
describe('choosing a basemap', () => {
  it('uses Amazon Location when the region and key are both set', () => {
    expect(initialMode(true)).toBe('location')
  })

  it('falls back when the configuration is missing', () => {
    expect(initialMode(false)).toBe('fallback')
  })

  it('starts with no style URL at all when nothing is configured', () => {
    // The test environment has no VITE_LOCATION_API_KEY, which is the case
    // this whole fallback exists for.
    expect(startingStyleUrl()).toBeNull()
    expect(initialMode()).toBe('fallback')
  })

  it('honours a ?style= override, and treats it as a style to try first', () => {
    const original = window.location.search
    window.history.replaceState({}, '', '/?style=/some-other-style.json')

    try {
      expect(styleOverride()).toBe('/some-other-style.json')
      expect(startingStyleUrl()).toBe('/some-other-style.json')
      // An override means there is something to try, so the map starts in
      // location mode and falls back only if that something fails.
      expect(initialMode()).toBe('location')
    } finally {
      window.history.replaceState({}, '', original || '/')
    }
  })

  it('builds the Amazon Location style URL from the configured values', () => {
    const url = locationStyleUrl('ap-south-1', 'secret-key')

    expect(url).toContain('maps.geo.ap-south-1.amazonaws.com')
    expect(url).toContain('/v2/styles/Standard/descriptor')
    expect(url).toContain('key=secret-key')
  })
})

// ------------------------------------------------------ the offline style
describe('the offline style', () => {
  it('makes no network request of any kind', () => {
    const style = fallbackStyle(SAMPLE_BASEMAP)

    expect(styleIsSelfContained(style)).toBe(true)
    expect(JSON.stringify(style)).not.toMatch(/https?:\/\//i)
  })

  it('has no glyphs and no sprite, so it needs no font or icon server', () => {
    const style = fallbackStyle(SAMPLE_BASEMAP) as Record<string, unknown>

    expect(style.glyphs).toBeUndefined()
    expect(style.sprite).toBeUndefined()
  })

  it('draws no text at all', () => {
    const style = fallbackStyle(SAMPLE_BASEMAP)

    expect(style.layers.some((layer) => layer.type === 'symbol')).toBe(false)
  })

  it('inlines the backdrop instead of pointing at a file', () => {
    const style = fallbackStyle(SAMPLE_BASEMAP)
    const source = style.sources.basemap as { type: string; data: unknown }

    expect(source.type).toBe('geojson')
    expect(source.data).toBe(SAMPLE_BASEMAP)
  })

  it('still works when there is no backdrop file', () => {
    const style = fallbackStyle(null)
    const source = style.sources.basemap as { data: { features: unknown[] } }

    expect(source.data.features).toEqual([])
    expect(style.layers[0].type).toBe('background')
    expect(styleIsSelfContained(style)).toBe(true)
  })

  it('separates roads from water, and major roads from minor', () => {
    const ids = fallbackStyle(SAMPLE_BASEMAP).layers.map((layer) => layer.id)

    expect(ids).toContain('water-body')
    expect(ids).toContain('road-major')
    expect(ids).toContain('road-minor')
    // Background first, so everything else draws over it.
    expect(ids[0]).toBe('paper')
  })

  it('detects a style that would reach the network', () => {
    expect(
      styleIsSelfContained({ glyphs: 'https://fonts.example/{range}.pbf' }),
    ).toBe(false)
    expect(styleIsSelfContained({ sources: { a: { url: 'http://tiles.example' } } })).toBe(
      false,
    )
  })
})

// --------------------------------------------------------- loading it in
describe('loading the backdrop', () => {
  it('returns the parsed file', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => SAMPLE_BASEMAP,
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(loadBasemap('/data/basemap.geojson')).resolves.toEqual(SAMPLE_BASEMAP)
    vi.unstubAllGlobals()
  })

  it('returns null rather than throwing when the file is not there', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }))
    await expect(loadBasemap()).resolves.toBeNull()

    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
    await expect(loadBasemap()).resolves.toBeNull()

    vi.unstubAllGlobals()
  })

  it('reads the attribution the generator recorded', () => {
    expect(basemapAttribution(SAMPLE_BASEMAP)).toContain('OpenStreetMap')
    expect(basemapAttribution(null)).toBeNull()
    expect(basemapAttribution({})).toBeNull()
  })
})

// --------------------------------------------------------- drain colours
describe('drain colours', () => {
  it('maps each verdict to its own colour', () => {
    expect(colourFor('RED')).toBe(COLOURS.RED)
    expect(colourFor('AMBER')).toBe(COLOURS.AMBER)
    expect(colourFor('GREEN')).toBe(COLOURS.GREEN)
  })

  it('leaves anything unverified grey', () => {
    expect(colourFor(null)).toBe(COLOURS.PENDING)
    expect(colourFor(undefined)).toBe(COLOURS.PENDING)
    expect(colourFor('SOMETHING ELSE')).toBe(COLOURS.PENDING)
  })

  it('gives every verdict a distinct colour', () => {
    const used = [COLOURS.RED, COLOURS.AMBER, COLOURS.GREEN, COLOURS.PENDING]
    expect(new Set(used).size).toBe(4)
  })
})

// ---------------------------------------------------------------- bounds
describe('fitting the viewport', () => {
  it('covers every ring of every feature', () => {
    const bounds = boundsOf([
      {
        features: [
          { geometry: { coordinates: [[[72.85, 19.05], [72.86, 19.06]]] } },
          { geometry: { coordinates: [[[72.9, 19.1], [72.91, 19.12]]] } },
        ],
      },
    ])

    expect(bounds).toEqual([
      [72.85, 19.05],
      [72.91, 19.12],
    ])
  })

  it('returns null when there is nothing to fit', () => {
    expect(boundsOf([{ features: [] }])).toBeNull()
  })
})
