// Map features for the trial map, as a pure function so it can be tested
// without WebGL.
import type * as GeoJSON from 'geojson'
import type { DisposalSite, DrainLocation, LonLat } from './types'

export function circleRing(point: LonLat, radiusM: number, segments = 48): LonLat[] {
  const ring: LonLat[] = []
  const mPerDegLat = 111_320
  const mPerDegLon = mPerDegLat * Math.cos((point[1] * Math.PI) / 180)
  for (let index = 0; index <= segments; index += 1) {
    const angle = (2 * Math.PI * index) / segments
    ring.push([
      point[0] + (radiusM * Math.cos(angle)) / mPerDegLon,
      point[1] + (radiusM * Math.sin(angle)) / mPerDegLat,
    ])
  }
  return ring
}

export function trialFeatures(props: {
  drain?: DrainLocation
  disposal?: DisposalSite
  photos: Array<{ id: string; point: LonLat; role: string }>
  routes: Array<{ id: string; route: LonLat[] }>
}) {
  const features: GeoJSON.Feature[] = []
  const { drain, disposal, photos, routes } = props
  if (disposal) {
    const ring = disposal.polygon ?? circleRing(disposal.point, disposal.radiusM)
    features.push({ type: 'Feature', properties: { kind: 'disposal-area' }, geometry: { type: 'Polygon', coordinates: [ring] } })
    features.push({ type: 'Feature', properties: { kind: 'disposal-point' }, geometry: { type: 'Point', coordinates: disposal.point } })
  }
  if (drain) {
    features.push({
      type: 'Feature',
      properties: { kind: 'tolerance' },
      geometry: { type: 'Polygon', coordinates: [circleRing(drain.point, drain.toleranceM)] },
    })
    if (drain.line) {
      features.push({ type: 'Feature', properties: { kind: 'drain-line' }, geometry: { type: 'LineString', coordinates: drain.line } })
    }
    features.push({ type: 'Feature', properties: { kind: 'drain-point' }, geometry: { type: 'Point', coordinates: drain.point } })
  }
  for (const route of routes) {
    features.push({ type: 'Feature', properties: { kind: 'trace', id: route.id }, geometry: { type: 'LineString', coordinates: route.route } })
  }
  for (const photo of photos) {
    features.push({ type: 'Feature', properties: { kind: 'photo', role: photo.role, id: photo.id }, geometry: { type: 'Point', coordinates: photo.point } })
  }
  return { type: 'FeatureCollection' as const, features }
}

