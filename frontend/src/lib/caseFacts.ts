import type { Drain, Finding, Trip } from '../types'

/**
 * Everything the case file says about a drain, derived from the API's drain
 * record rather than typed in, so drain 14 and every other drain open the
 * same way.
 */

// Which finding is worth a one-line reason, most damning first.
export const RULE_ORDER = [
  'R3', 'R5', 'R8', 'R4', 'R7', 'R6', 'R9', 'R2', 'R1', 'R10',
  'GPS_GAP', 'SLIP_MISSING', 'TRACE_MISSING', 'PHOTOS_MISSING', 'EVIDENCE_ERROR',
]

const ROUTE_RULES = ['R5', 'R6', 'GPS_GAP', 'TRACE_MISSING']
const SLIP_RULES = ['R7', 'R8', 'R9', 'SLIP_MISSING']
const PHOTO_RULES = ['R1', 'R2', 'R3', 'R4', 'PHOTOS_MISSING']

export type LngLat = [number, number]

/** Every finding on the drain, its own and its trips', one per rule and message. */
export function allFindings(drain: Drain): Finding[] {
  const seen = new Set<string>()
  const out: Finding[] = []
  for (const finding of [...drain.findings, ...drain.trips.flatMap((trip) => trip.findings)]) {
    const key = `${finding.rule}|${finding.message}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push(finding)
  }
  return out
}

/** One plain sentence for why a drain is flagged, from its weightiest finding. */
export function reasonFor(drain: Drain): string {
  const findings = allFindings(drain)
  for (const rule of RULE_ORDER) {
    const hit = findings.find((finding) => finding.rule === rule)
    if (hit) return hit.message
  }
  return findings[0]?.message ?? ''
}

/** The trip the case file opens on: the first held one, else the first in review. */
export function focusTrip(drain: Drain, tripId: string | null = null): Trip | null {
  const chosen = tripId ? drain.trips.find((trip) => trip.tripId === tripId) : undefined
  if (chosen) return chosen
  return (
    drain.trips.find((trip) => trip.verdict === 'HOLD') ??
    drain.trips.find((trip) => trip.verdict === 'REVIEW') ??
    drain.trips[0] ??
    null
  )
}

export function metres([x1, y1]: LngLat, [x2, y2]: LngLat) {
  const R = 6371000
  const toR = Math.PI / 180
  const dLat = (y2 - y1) * toR
  const dLon = (x2 - x1) * toR
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(y1 * toR) * Math.cos(y2 * toR) * Math.sin(dLon / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(a))
}

export function lineLength(coords: LngLat[]) {
  let total = 0
  for (let i = 1; i < coords.length; i++) total += metres(coords[i - 1], coords[i])
  return total
}

/** The first `frac` of a line by distance, ending on an interpolated point. */
export function sliceLine(coords: LngLat[], frac: number): LngLat[] {
  if (frac >= 1 || coords.length < 2) return coords
  const target = lineLength(coords) * Math.max(0, frac)
  const out: LngLat[] = [coords[0]]
  let run = 0
  for (let i = 1; i < coords.length; i++) {
    const seg = metres(coords[i - 1], coords[i])
    if (run + seg >= target) {
      const t = seg ? (target - run) / seg : 0
      out.push([
        coords[i - 1][0] + (coords[i][0] - coords[i - 1][0]) * t,
        coords[i - 1][1] + (coords[i][1] - coords[i - 1][1]) * t,
      ])
      return out
    }
    run += seg
    out.push(coords[i])
  }
  return out
}

export function bearingTo(a: LngLat, b: LngLat) {
  const k = Math.cos((a[1] * Math.PI) / 180)
  return (Math.atan2((b[0] - a[0]) * k, b[1] - a[1]) * 180) / Math.PI
}

/** "07:12" from an ISO time, read as written (the data is in IST). */
export function hhmm(iso: string | null | undefined): string | null {
  if (!iso) return null
  const match = /T(\d{2}:\d{2})/.exec(iso)
  return match ? match[1] : null
}

export function minutesOf(time: string) {
  const [h, m] = time.split(':').map(Number)
  return h * 60 + m
}

export interface R8Facts {
  timeIn: string
  /** When the truck left the drain, from the GPS trace. */
  departure: string | null
  /** The GPS time R8 compared against: a dump-site arrival, or the last fix. */
  compared: string | null
  comparedKind: 'arrival' | 'lastFix'
  /** Whole minutes, truncated the way the finding's message reads. */
  minutesApart: number
  message: string
}

export interface CaseFacts {
  trip: Trip | null
  tripFindings: Finding[]
  /** Where the focus trip's trace ends. */
  stop: LngLat | null
  dump: LngLat
  /** False when R5 says the focus trip's trace never enters the dump site. */
  reachedDump: boolean
  /** Every trip failed R5, and their traces end within 300 m of each other. */
  allStopTogether: boolean
  shortOfDumpM: number | null
  claimedKm: number | null
  drivenKm: number | null
  r8: R8Facts | null
  r3: Finding | null
  holdTrips: number
  reviewTrips: number
  hardRules: string[]
  softRules: string[]
  routeRules: string[]
  slipRules: string[]
  photoRules: string[]
  conflictingField: string | null
}

function rulesIn(findings: Finding[], family: string[]) {
  return [...new Set(findings.map((f) => f.rule).filter((rule) => family.includes(rule)))]
}

/** The slip field a rule is arguing with, so it can be highlighted. */
export function conflictingField(trip: Trip | null): string | null {
  if (!trip) return null
  const rules = [...trip.hardFails, ...trip.softFails]
  if (rules.includes('R8')) return 'timeIn'
  if (rules.includes('R7')) return 'net'
  if (rules.includes('R9')) return 'vehicleNo'
  return null
}

export function caseFacts(drain: Drain, tripId: string | null = null): CaseFacts {
  const trip = focusTrip(drain, tripId)
  const tripFindings = trip?.findings ?? []
  const findings = allFindings(drain)
  const dump = drain.dumpsite.center
  const route = trip?.actualRoute ?? []
  const tripEnd: LngLat | null = route.length ? route[route.length - 1] : null
  const failedR5 = (t: Trip) => t.findings.some((f) => f.rule === 'R5')
  const reachedDump = trip ? !failedR5(trip) : true

  const ends = drain.trips
    .filter((t) => t.actualRoute.length)
    .map((t) => t.actualRoute[t.actualRoute.length - 1])
  const allStopTogether =
    drain.trips.length > 1 &&
    drain.trips.every(failedR5) &&
    tripEnd !== null &&
    ends.every((end) => metres(end, tripEnd) < 300)
  // When every trace ends in the same place, "here" is the middle of them.
  const stop: LngLat | null = allStopTogether
    ? [
        ends.reduce((sum, p) => sum + p[0], 0) / ends.length,
        ends.reduce((sum, p) => sum + p[1], 0) / ends.length,
      ]
    : tripEnd

  const r8Finding = tripFindings.find((f) => f.rule === 'R8')
  let r8: R8Facts | null = null
  if (r8Finding && trip) {
    const ev = r8Finding.evidence as {
      timeIn?: string
      arrival?: string
      arrivalKind?: string
      departure?: string
      minutesEarly?: number
    }
    r8 = {
      timeIn: ev.timeIn ?? trip.slip?.timeIn ?? '',
      departure: hhmm(ev.departure) ?? hhmm(trip.startTime),
      compared: hhmm(ev.arrival),
      comparedKind: ev.arrivalKind === 'lastFix' ? 'lastFix' : 'arrival',
      minutesApart: Math.trunc(Math.abs(ev.minutesEarly ?? 0)),
      message: r8Finding.message,
    }
  }

  const r3 =
    findings.find((f) => f.rule === 'R3' && f.evidence.hammingDistance === 0) ??
    findings.find((f) => f.rule === 'R3') ??
    null

  return {
    trip,
    tripFindings,
    stop,
    dump,
    reachedDump,
    allStopTogether,
    shortOfDumpM: stop && !reachedDump ? metres(stop, dump) : null,
    claimedKm: drain.claimedRoute ? lineLength(drain.claimedRoute) / 1000 : null,
    drivenKm: trip ? trip.actualRouteDistanceM / 1000 : null,
    r8,
    r3,
    holdTrips: drain.trips.filter((t) => t.verdict === 'HOLD').length,
    reviewTrips: drain.trips.filter((t) => t.verdict === 'REVIEW').length,
    hardRules: [...new Set(findings.filter((f) => f.severity === 'hard').map((f) => f.rule))],
    softRules: [...new Set(findings.filter((f) => f.severity === 'soft').map((f) => f.rule))],
    routeRules: rulesIn(findings, ROUTE_RULES),
    slipRules: rulesIn(tripFindings.length ? tripFindings : findings, SLIP_RULES),
    photoRules: rulesIn(findings, PHOTO_RULES),
    conflictingField: conflictingField(trip),
  }
}

// ----------------------------------------------------------------- titles
// Each exhibit's headline states what its evidence shows, or says plainly
// that nothing was found.

export function routeTitle(drain: Drain, facts: CaseFacts): string {
  if (facts.trip && !facts.reachedDump) {
    return facts.allStopTogether
      ? 'No truck reached the dump site'
      : `Trip ${facts.trip.tripNo} never reached the dump site`
  }
  if (facts.routeRules.includes('R6')) return 'One truck, logged in two places at once'
  if (facts.routeRules.includes('GPS_GAP')) return 'The GPS trace goes dark on the way'
  if (facts.routeRules.includes('TRACE_MISSING')) return 'A trip has no GPS trace'
  return drain.verdict ? 'Every trace reaches the dump site' : 'The haul to the dump site'
}

export function slipTitle(facts: CaseFacts): string {
  const r8 = facts.r8
  if (r8 && r8.departure && minutesOf(r8.timeIn) < minutesOf(r8.departure)) {
    return 'The slip is stamped before the truck left'
  }
  if (r8) return 'The slip time does not match the GPS'
  if (facts.slipRules.includes('R7')) return 'The slip claims more than the truck carries'
  if (facts.slipRules.includes('R9')) return 'The slip names a different truck'
  if (facts.slipRules.includes('SLIP_MISSING')) return 'No weighbridge slip for this trip'
  return 'The weighbridge slip'
}

export function photoTitle(facts: CaseFacts): string {
  if (facts.r3) {
    const original = facts.r3.evidence.originalDrainId
    return original
      ? `The after-photo was filed for drain ${original} first`
      : 'A photo was filed twice'
  }
  if (facts.photoRules.includes('R4')) return 'The photographed load is not drain silt'
  if (facts.photoRules.includes('R1')) return 'A photo was taken outside the drain'
  if (facts.photoRules.includes('R2')) return 'A photo was taken outside the work window'
  if (facts.photoRules.includes('PHOTOS_MISSING')) return 'No photographs were filed'
  return 'The photographs'
}
