import type { CaseFacts } from './caseFacts'
import { hhmm, minutesOf } from './caseFacts'
import type { Drain } from '../types'

/** Mirrors backend/common/rules.py, so the screen can state the thresholds. */
export const SLIP_TOLERANCE_MIN = 10 // R8
export const PHASH_DUPLICATE_MAX = 12 // R3

export interface TimelineEvent {
  time: string
  text: string
  bad: boolean
  shape: 'dot' | 'diamond'
}

export interface Lane {
  name: string
  sub: string
  events: TimelineEvent[]
  /** Said in the gutter after the axis, e.g. "Never arrives at the dump site". */
  end: { strong: string; rest: string } | null
}

export interface Bracket {
  kind: 'lead' | 'r8'
  from: string
  to: string
  text: string
  minutes: number
}

export interface Timeline {
  lanes: Lane[]
  lo: number
  hi: number
  ticks: number[]
  brackets: Bracket[]
}

/**
 * The two-lane timeline of the focus trip: what the slip says, and what the
 * GPS trace says. R8's times are kept apart: the slip's time-in, the GPS
 * departure from the drain, the last GPS fix, and whether the truck ever
 * arrived at the dump site at all. A last fix is never drawn as an arrival.
 */
export function buildTimeline(drain: Drain, facts: CaseFacts): Timeline | null {
  const trip = facts.trip
  if (!trip) return null

  const lanes: Lane[] = []
  const slip = trip.slip
  if (slip && (slip.timeIn || slip.timeOut)) {
    const events: TimelineEvent[] = []
    if (slip.timeIn) {
      events.push({
        time: slip.timeIn,
        text: 'time in',
        bad: facts.conflictingField === 'timeIn',
        shape: 'diamond',
      })
    }
    if (slip.timeOut) events.push({ time: slip.timeOut, text: 'time out', bad: false, shape: 'diamond' })
    lanes.push({
      name: 'Weighbridge slip',
      sub: slip.ticketNo ? `ticket ${slip.ticketNo}` : 'ticket not read',
      events,
      end: null,
    })
  }

  const gps: TimelineEvent[] = []
  const departure = facts.r8?.departure ?? hhmm(trip.startTime)
  if (departure) {
    gps.push({ time: departure, text: `leaves drain ${drain.drainId}`, bad: false, shape: 'dot' })
  }
  const arrival = hhmm(trip.arrivalTime)
  if (facts.reachedDump) {
    if (arrival) gps.push({ time: arrival, text: 'arrives at the dump site', bad: false, shape: 'dot' })
  } else {
    if (arrival) {
      const short = facts.shortOfDumpM !== null ? `${(facts.shortOfDumpM / 1000).toFixed(1)} km short` : 'short'
      gps.push({ time: arrival, text: `stops ${short}`, bad: true, shape: 'dot' })
    }
    const lastFix = facts.r8?.comparedKind === 'lastFix' ? facts.r8.compared : null
    if (lastFix && lastFix !== arrival) gps.push({ time: lastFix, text: 'last fix', bad: true, shape: 'dot' })
  }
  if (gps.length) {
    lanes.push({
      name: 'GPS trace',
      sub: `trip ${trip.tripNo}, ${trip.vehicleNo}`,
      events: gps,
      end: facts.reachedDump ? null : { strong: 'Never arrives', rest: 'at the dump site' },
    })
  }

  const all = lanes.flatMap((lane) => lane.events.map((event) => minutesOf(event.time)))
  if (!all.length) return null
  const lo = Math.floor((Math.min(...all) - 5) / 15) * 15
  const hi = Math.ceil((Math.max(...all) + 5) / 15) * 15
  const ticks: number[] = []
  for (let m = lo; m <= hi; m += 15) ticks.push(m)

  const brackets: Bracket[] = []
  const r8 = facts.r8
  if (r8 && r8.departure && minutesOf(r8.timeIn) < minutesOf(r8.departure)) {
    const lead = minutesOf(r8.departure) - minutesOf(r8.timeIn)
    brackets.push({
      kind: 'lead',
      from: r8.timeIn,
      to: r8.departure,
      minutes: lead,
      text: `Slip time-in is ${lead} min before the truck leaves the drain`,
    })
  }
  if (r8 && r8.compared) {
    const against = r8.comparedKind === 'lastFix' ? 'the last GPS fix' : 'the GPS arrival at the dump site'
    brackets.push({
      kind: 'r8',
      from: r8.timeIn,
      to: r8.compared,
      minutes: r8.minutesApart,
      text: `R8: slip time-in against ${against}, ${r8.minutesApart} min apart. Allowed: ${SLIP_TOLERANCE_MIN} min.`,
    })
  }

  return { lanes, lo, hi, ticks, brackets }
}

export function clock(minutes: number) {
  return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`
}

export interface Placement {
  row: 'up' | 'down'
  side: 'l' | 'r'
  x0: number
  x1: number
}

/**
 * Place each event's label above or below its dot, to its right or left, so
 * no two labels on a lane collide and none leaves the track. `xs` are the
 * dots' positions in px, `widths` the measured label widths.
 */
export function placeLabels(xs: number[], widths: number[], trackWidth: number, gap = 14): Placement[] {
  const optionsFor = (px: number, w: number): Placement[] => {
    const all: Placement[] = [
      { row: 'up', side: 'l', x0: px + 2, x1: px + 2 + w },
      { row: 'up', side: 'r', x0: px - 2 - w, x1: px - 2 },
      { row: 'down', side: 'l', x0: px + 2, x1: px + 2 + w },
      { row: 'down', side: 'r', x0: px - 2 - w, x1: px - 2 },
    ]
    const fits = all.filter((o) => o.x0 >= -4 && o.x1 <= trackWidth + 4)
    return fits.length ? fits : all
  }
  const clash = (a: Placement, b: Placement) =>
    a.row === b.row && a.x0 < b.x1 + gap && a.x1 > b.x0 - gap
  const options = xs.map((px, index) => optionsFor(px, widths[index]))

  // A lane holds a handful of events, so search every combination in order
  // of preference and take the first with no collision.
  const search = (index: number, chosen: Placement[]): Placement[] | null => {
    if (index === options.length) return chosen
    for (const option of options[index]) {
      if (chosen.some((other) => clash(option, other))) continue
      const found = search(index + 1, [...chosen, option])
      if (found) return found
    }
    return null
  }
  return search(0, []) ?? options.map((list) => list[0])
}
