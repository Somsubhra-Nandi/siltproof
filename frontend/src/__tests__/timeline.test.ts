import { describe, expect, it } from 'vitest'

import { caseFacts } from '../lib/caseFacts'
import { buildTimeline, placeLabels } from '../lib/timeline'
import type { Drain } from '../types'
import d14 from '../../public/data/demo/drain-14.json'
import d1 from '../../public/data/demo/drain-1.json'

const drain14 = d14 as unknown as Drain

describe("drain 14's timeline", () => {
  const timeline = buildTimeline(drain14, caseFacts(drain14))!

  it('puts the slip and the GPS trace on separate lanes', () => {
    expect(timeline.lanes.map((lane) => lane.name)).toEqual(['Weighbridge slip', 'GPS trace'])
  })

  it('keeps departure, the stop and the last fix apart, and never shows an arrival', () => {
    const gps = timeline.lanes[1]
    expect(gps.events.map((e) => [e.time, e.text])).toEqual([
      ['07:12', 'leaves drain 14'],
      ['07:33', 'stops 2.2 km short'],
      ['07:37', 'last fix'],
    ])
    expect(gps.events.some((e) => /arrive/.test(e.text))).toBe(false)
    expect(gps.end).toEqual({ strong: 'Never arrives', rest: 'at the dump site' })
  })

  it('marks the slip time-in as the disputed event', () => {
    expect(timeline.lanes[0].events[0]).toMatchObject({ time: '06:53', bad: true })
  })

  it("brackets the minutes the R8 message states", () => {
    const [lead, r8] = timeline.brackets
    expect(lead).toMatchObject({ kind: 'lead', from: '06:53', to: '07:12', minutes: 19 })
    expect(r8).toMatchObject({ kind: 'r8', from: '06:53', to: '07:37', minutes: 44 })
    expect(r8.text).toContain('the last GPS fix')
    expect(drain14.trips[0].findings.find((f) => f.rule === 'R8')!.message).toContain('44 minutes')
    expect(drain14.trips[0].findings.find((f) => f.rule === 'R8')!.message).toContain('19 minutes')
  })

  it('derives a 15-minute axis around the data', () => {
    expect(timeline.lo).toBe(6 * 60 + 45)
    expect(timeline.hi).toBe(7 * 60 + 45)
    expect(timeline.ticks).toHaveLength(5)
  })
})

describe('a clean drain', () => {
  it('shows the arrival and no brackets', () => {
    const drain = d1 as unknown as Drain
    const timeline = buildTimeline(drain, caseFacts(drain))!
    expect(timeline.lanes[1].events.map((e) => e.text)).toContain('arrives at the dump site')
    expect(timeline.lanes[1].end).toBeNull()
    expect(timeline.brackets).toEqual([])
  })
})

describe('label placement', () => {
  const overlaps = (a: { row: string; x0: number; x1: number }, b: typeof a) =>
    a.row === b.row && a.x0 < b.x1 && a.x1 > b.x0

  it('separates labels four minutes apart', () => {
    // 07:33 and 07:37 on a 60-minute axis 720 px wide: 48 px apart.
    const placed = placeLabels([576, 624], [150, 90], 720)
    expect(overlaps(placed[0], placed[1])).toBe(false)
  })

  it('flips a label at the end of the axis to the left of its dot', () => {
    const [last] = placeLabels([716], [120], 720)
    expect(last.side).toBe('r')
    expect(last.x1).toBeLessThanOrEqual(720)
  })

  it('keeps three crowded labels apart', () => {
    const placed = placeLabels([300, 330, 360], [120, 120, 120], 720)
    for (let i = 0; i < placed.length; i++)
      for (let j = i + 1; j < placed.length; j++) expect(overlaps(placed[i], placed[j])).toBe(false)
  })
})
