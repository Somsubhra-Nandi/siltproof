// Pure helpers for the trial screens. Every check here is repeated on the
// server; these exist only to answer the judge sooner.

import type {
  Check,
  CheckStatus,
  Evidence,
  EvidenceState,
  Group,
  Limits,
  LonLat,
  Trial,
} from './types'

export const PENDING_STATES: EvidenceState[] = ['UPLOADED', 'QUEUED', 'PROCESSING']

export function hasPending(trial: Trial | null): boolean {
  return Boolean(trial?.evidence.some((item) => PENDING_STATES.includes(item.state)))
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const BY_EXTENSION: Record<string, string> = {
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  pdf: 'application/pdf',
  json: 'application/json',
  geojson: 'application/geo+json',
}

/** The browser's type, or one inferred from the extension when it gives none. */
export function contentTypeFor(file: { name: string; type: string }): string {
  const type = (file.type || '').toLowerCase()
  if (type === 'image/jpg') return 'image/jpeg'
  if (type) return type
  const extension = file.name.split('.').pop()?.toLowerCase() ?? ''
  return BY_EXTENSION[extension] ?? ''
}

export function checkFile(
  file: { name: string; type: string; size: number },
  group: Group,
  limits: Limits,
  existing: Evidence[],
): string | null {
  const spec = limits.groups[group]
  const type = contentTypeFor(file)
  if (!spec.contentTypes.includes(type)) {
    return `${file.name}: ${type || 'unknown type'} is not accepted here (${spec.contentTypes.join(', ')}).`
  }
  if (file.size === 0) return `${file.name} is empty.`
  if (file.size > spec.maxBytes) {
    return `${file.name} is ${formatBytes(file.size)}; the limit is ${formatBytes(spec.maxBytes)}.`
  }
  const inGroup = existing.filter((item) => item.group === group && item.state !== 'REJECTED').length
  if (inGroup >= spec.maxFiles) return `A trial holds at most ${spec.maxFiles} files of this kind.`
  return null
}

/** "22.5801, 88.4712" (latitude, longitude, as maps copy it) -> [lon, lat]. */
export function parseLatLon(text: string): LonLat | null {
  const match = text.trim().match(/^(-?\d+(?:\.\d+)?)\s*[,\s]\s*(-?\d+(?:\.\d+)?)$/)
  if (!match) return null
  const lat = Number(match[1])
  const lon = Number(match[2])
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null
  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return null
  if (lat === 0 && lon === 0) return null
  return [lon, lat]
}

export function formatLatLon(point: LonLat | undefined | null): string {
  if (!point) return ''
  return `${point[1].toFixed(6)}, ${point[0].toFixed(6)}`
}

export type Readiness = 'missing' | 'pending' | 'ready' | 'problem'

export function groupReadiness(trial: Trial, group: Group): Readiness {
  const items = trial.evidence.filter((item) => item.group === group)
  if (items.some((item) => PENDING_STATES.includes(item.state) || item.state === 'UPLOADING')) {
    return 'pending'
  }
  if (items.some((item) => item.state === 'READY')) return 'ready'
  if (items.some((item) => item.state === 'FAILED' || item.state === 'REJECTED')) return 'problem'
  return 'missing'
}

export function billReadiness(trial: Trial): Readiness {
  const claim = trial.details.claim
  const doc = groupReadiness(trial, 'bill')
  if (claim) return doc === 'pending' ? 'pending' : 'ready'
  return doc === 'missing' ? 'missing' : doc
}

export const READINESS_LABEL: Record<Readiness, string> = {
  missing: 'Not supplied',
  pending: 'Processing',
  ready: 'Supplied',
  problem: 'Needs attention',
}

export const STATE_LABEL: Record<EvidenceState, string> = {
  UPLOADING: 'Uploading',
  UPLOADED: 'Uploaded, checking',
  QUEUED: 'Queued',
  PROCESSING: 'Processing',
  READY: 'Processed',
  FAILED: 'Failed',
  REJECTED: 'Rejected',
}

export const STATUS_LABEL: Record<CheckStatus, string> = {
  PASS: 'PASS',
  CONSISTENT: 'CONSISTENT, NOT PROOF',
  REVIEW: 'REVIEW',
  FAIL: 'FAIL',
  INCONCLUSIVE: 'INCONCLUSIVE',
  NOT_EVALUATED: 'NOT EVALUATED — INSUFFICIENT EVIDENCE',
}

export type CheckSection = { key: string; title: string; checks: Check[] }

export function sectionChecks(checks: Check[]): CheckSection[] {
  const pick = (statuses: CheckStatus[]) => checks.filter((check) => statuses.includes(check.status))
  return [
    { key: 'problems', title: 'Findings against the claim', checks: pick(['FAIL', 'REVIEW']) },
    { key: 'clear', title: 'Checks with nothing against the claim', checks: pick(['PASS', 'CONSISTENT']) },
    { key: 'inconclusive', title: 'Inconclusive', checks: pick(['INCONCLUSIVE']) },
    { key: 'missing', title: 'Not evaluated — insufficient evidence', checks: pick(['NOT_EVALUATED']) },
  ].filter((section) => section.checks.length > 0)
}

/**
 * An approximate line through the photos' GPS fixes, in capture order, with
 * repeated fixes dropped. It is circular to check those same photos against
 * it, which is why it is saved with derivedFromPhotos = true.
 */
export function lineFromPhotos(evidence: Evidence[]): LonLat[] {
  const fixes = evidence
    .filter((item) => item.group === 'photo' && item.result?.exif?.hasGps)
    .map((item) => ({
      t: item.result?.exif?.timestamp ?? '',
      point: [item.result!.exif!.lon!, item.result!.exif!.lat!] as LonLat,
    }))
    .sort((a, b) => a.t.localeCompare(b.t))
  const line: LonLat[] = []
  for (const fix of fixes) {
    const last = line[line.length - 1]
    if (!last || last[0] !== fix.point[0] || last[1] !== fix.point[1]) line.push(fix.point)
  }
  return line
}

/** The claim arithmetic hint, the same tolerance as the server's T1. */
export function claimArithmetic(quantity?: number, rate?: number, amount?: number): string | null {
  if (quantity === undefined || rate === undefined || amount === undefined) return null
  const expected = quantity * rate
  const tolerance = Math.max(1, 0.005 * expected)
  if (Math.abs(expected - amount) <= tolerance) return null
  return `${quantity} t × ₹${rate.toLocaleString('en-IN')} = ₹${expected.toLocaleString('en-IN', {
    maximumFractionDigits: 2,
  })}, not ₹${amount.toLocaleString('en-IN')}. You can still save it; the check will report it.`
}

export function optionalNumber(text: string): number | undefined | null {
  const trimmed = text.trim()
  if (trimmed === '') return undefined
  const value = Number(trimmed)
  return Number.isFinite(value) ? value : null
}

export function photoPoints(evidence: Evidence[]): Array<{ id: string; point: LonLat; role: string }> {
  return evidence
    .filter((item) => item.group === 'photo' && item.result?.exif?.hasGps)
    .map((item) => ({
      id: item.evidenceId,
      point: [item.result!.exif!.lon!, item.result!.exif!.lat!] as LonLat,
      role: item.role ?? 'current',
    }))
}

export function traceRoutes(evidence: Evidence[]): Array<{ id: string; route: LonLat[] }> {
  return evidence
    .filter((item) => item.group === 'trace' && item.state === 'READY' && item.result?.route?.length)
    .map((item) => ({ id: item.evidenceId, route: item.result!.route! }))
}

export function hoursLeft(expiresAt: string, now = Date.now()): number {
  return Math.max(0, Math.round((Date.parse(expiresAt) - now) / 3_600_000))
}
