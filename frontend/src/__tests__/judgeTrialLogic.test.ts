import { describe, expect, it } from 'vitest'

import {
  STATUS_LABEL,
  checkFile,
  claimArithmetic,
  contentTypeFor,
  groupReadiness,
  hasPending,
  lineFromPhotos,
  parseLatLon,
  sectionChecks,
} from '../features/judge-trial/logic'
import { circleRing, trialFeatures } from '../features/judge-trial/mapData'
import type { Check, Evidence, Limits, Trial } from '../features/judge-trial/types'

const MB = 1024 * 1024
const LIMITS: Limits = {
  maxFilesPerTrial: 24,
  maxBytesPerTrial: 120 * MB,
  maxRetries: 2,
  maxAnalyses: 30,
  photoRoles: ['before', 'after', 'current', 'additional'],
  groups: {
    photo: { contentTypes: ['image/jpeg', 'image/png'], maxBytes: 15 * MB, maxFiles: 12 },
    slip: { contentTypes: ['application/pdf', 'image/jpeg', 'image/png'], maxBytes: 10 * MB, maxFiles: 6 },
    bill: { contentTypes: ['application/pdf', 'image/jpeg', 'image/png'], maxBytes: 10 * MB, maxFiles: 2 },
    trace: { contentTypes: ['application/geo+json', 'application/json'], maxBytes: 2 * MB, maxFiles: 4 },
  },
}

function evidence(patch: Partial<Evidence>): Evidence {
  return {
    evidenceId: 'ev_x',
    group: 'photo',
    role: 'current',
    filename: 'a.jpg',
    contentType: 'image/jpeg',
    declaredSizeBytes: 10,
    sizeBytes: 10,
    sha256: null,
    state: 'READY',
    error: null,
    attempts: 1,
    createdAt: '',
    updatedAt: '',
    result: null,
    ...patch,
  }
}

function trial(items: Evidence[], details = {}): Trial {
  return { evidence: items, details, limits: LIMITS } as unknown as Trial
}

describe('client-side file checks (the server repeats them)', () => {
  it('accepts a 10 MB+ original JPEG and refuses what the API refuses', () => {
    expect(checkFile({ name: 'IMG.jpg', type: 'image/jpeg', size: 12 * MB }, 'photo', LIMITS, [])).toBeNull()
    expect(checkFile({ name: 'IMG.heic', type: 'image/heic', size: 10 }, 'photo', LIMITS, [])).toMatch(/not accepted/)
    expect(checkFile({ name: 'big.jpg', type: 'image/jpeg', size: 16 * MB }, 'photo', LIMITS, [])).toMatch(/limit/)
    expect(checkFile({ name: 'route.gpx', type: 'application/gpx+xml', size: 10 }, 'trace', LIMITS, [])).toMatch(/not accepted/)
    expect(checkFile({ name: 'empty.pdf', type: 'application/pdf', size: 0 }, 'slip', LIMITS, [])).toMatch(/empty/)
  })

  it('infers a type from the extension when the browser gives none', () => {
    expect(contentTypeFor({ name: 'route.geojson', type: '' })).toBe('application/geo+json')
    expect(contentTypeFor({ name: 'IMG.JPG', type: '' })).toBe('image/jpeg')
    expect(contentTypeFor({ name: 'x.jpg', type: 'image/jpg' })).toBe('image/jpeg')
  })

  it('counts files already in the group', () => {
    const bills = [evidence({ group: 'bill' }), evidence({ group: 'bill' })]
    expect(checkFile({ name: 'b.pdf', type: 'application/pdf', size: 10 }, 'bill', LIMITS, bills)).toMatch(/at most 2/)
  })
})

describe('coordinates', () => {
  it('reads "latitude, longitude" and stores [lon, lat]', () => {
    expect(parseLatLon('22.5801, 88.4712')).toEqual([88.4712, 22.5801])
    expect(parseLatLon('22.5801 88.4712')).toEqual([88.4712, 22.5801])
    expect(parseLatLon('95, 10')).toBeNull()
    expect(parseLatLon('0, 0')).toBeNull()
    expect(parseLatLon('Kolkata')).toBeNull()
  })

  it('draws an approximate line through the photo fixes in capture order', () => {
    const photo = (lat: number, lon: number, t: string) =>
      evidence({ result: { exif: { hasGps: true, lat, lon, timestamp: t, timestampHasOffset: false, problems: [] } } })
    const line = lineFromPhotos([
      photo(22.582, 88.472, '2026-10-09T09:58:00'),
      photo(22.58, 88.47, '2026-10-09T09:53:00'),
      photo(22.58, 88.47, '2026-10-09T09:54:00'), // same fix repeated
    ])
    expect(line).toEqual([[88.47, 22.58], [88.472, 22.582]])
  })
})

describe('readiness and polling', () => {
  it('reports each group honestly', () => {
    expect(groupReadiness(trial([]), 'photo')).toBe('missing')
    expect(groupReadiness(trial([evidence({ state: 'PROCESSING' })]), 'photo')).toBe('pending')
    expect(groupReadiness(trial([evidence({ state: 'FAILED' })]), 'photo')).toBe('problem')
    expect(groupReadiness(trial([evidence({})]), 'photo')).toBe('ready')
  })

  it('polls only while something is queued or processing', () => {
    expect(hasPending(trial([evidence({ state: 'QUEUED' })]))).toBe(true)
    expect(hasPending(trial([evidence({ state: 'READY' }), evidence({ state: 'UPLOADING' })]))).toBe(false)
    expect(hasPending(null)).toBe(false)
  })
})

describe('results', () => {
  const check = (status: Check['status']): Check => ({
    id: 'R1', title: 't', subject: null, status, severity: null, message: 'm', missing: [], basis: null, evidence: {},
  })

  it('keeps not-evaluated checks out of the passed section', () => {
    const sections = sectionChecks([check('PASS'), check('NOT_EVALUATED'), check('FAIL'), check('CONSISTENT')])
    expect(sections.map((section) => section.key)).toEqual(['problems', 'clear', 'missing'])
    expect(sections.find((section) => section.key === 'clear')!.checks.map((item) => item.status)).toEqual(['PASS', 'CONSISTENT'])
    expect(STATUS_LABEL.NOT_EVALUATED).toBe('NOT EVALUATED — INSUFFICIENT EVIDENCE')
  })

  it('mirrors the server claim-arithmetic tolerance', () => {
    expect(claimArithmetic(50, 1800, 90000)).toBeNull()
    expect(claimArithmetic(50, 1800, 90300)).toBeNull() // within 0.5 %
    expect(claimArithmetic(50, 1800, 95000)).toMatch(/not ₹95,000/)
    expect(claimArithmetic(50, undefined, 95000)).toBeNull()
  })
})

describe('map data', () => {
  it('draws the supplied drain, its tolerance, the site, photos and traces', () => {
    const data = trialFeatures({
      drain: { point: [88.47, 22.58], toleranceM: 30, source: 'manual_point', derivedFromPhotos: false },
      disposal: { point: [88.43, 22.56], radiusM: 200 },
      photos: [{ id: 'ev_1', point: [88.4701, 22.5801], role: 'after' }],
      routes: [{ id: 'ev_2', route: [[88.47, 22.58], [88.43, 22.56]] }],
    })
    const kinds = data.features.map((feature) => feature.properties?.kind)
    expect(kinds).toEqual(['disposal-area', 'disposal-point', 'tolerance', 'drain-point', 'trace', 'photo'])
  })

  it('makes a closed ring of the right size', () => {
    const ring = circleRing([88.47, 22.58], 100)
    expect(ring[0]).toEqual(ring[ring.length - 1])
    const eastMetres = (ring[0][0] - 88.47) * 111_320 * Math.cos((22.58 * Math.PI) / 180)
    expect(eastMetres).toBeCloseTo(100, 0)
  })
})
