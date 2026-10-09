import { describe, expect, it } from 'vitest'

import {
  caseFacts,
  photoTitle,
  reasonFor,
  routeTitle,
  sliceLine,
  slipTitle,
  lineLength,
} from '../lib/caseFacts'
import type { Drain } from '../types'
import d14 from '../../public/data/demo/drain-14.json'
import d6 from '../../public/data/demo/drain-6.json'
import d3 from '../../public/data/demo/drain-3.json'
import d11 from '../../public/data/demo/drain-11.json'
import d16 from '../../public/data/demo/drain-16.json'
import d1 from '../../public/data/demo/drain-1.json'

const drain = (json: unknown) => json as Drain

describe('drain 14, from the snapshot', () => {
  const facts = caseFacts(drain(d14))

  it('opens on the first held trip', () => {
    expect(facts.trip?.tripId).toBe('14#001')
  })

  it("keeps R8's four times apart: slip time-in, departure, last fix, no arrival", () => {
    expect(facts.r8).toMatchObject({
      timeIn: '06:53',
      departure: '07:12',
      compared: '07:37',
      comparedKind: 'lastFix',
      minutesApart: 44,
    })
    expect(facts.reachedDump).toBe(false)
  })

  it('agrees with the minutes in the finding message', () => {
    expect(facts.r8?.message).toContain(`${facts.r8?.minutesApart} minutes after`)
  })

  it('measures how far short the traces stop, and that they all stop together', () => {
    expect(facts.allStopTogether).toBe(true)
    expect(facts.shortOfDumpM! / 1000).toBeCloseTo(2.2, 1)
  })

  it('finds the reused photo', () => {
    expect(facts.r3?.evidence.originalDrainId).toBe('9')
    expect(facts.r3?.evidence.hammingDistance).toBe(0)
  })

  it('titles its exhibits from the findings', () => {
    expect(routeTitle(drain(d14), facts)).toBe('No truck reached the dump site')
    expect(slipTitle(facts)).toBe('The slip is stamped before the truck left')
    expect(photoTitle(facts)).toBe('The after-photo was filed for drain 9 first')
  })

  it('highlights the slip time-in', () => {
    expect(facts.conflictingField).toBe('timeIn')
  })
})

describe('other drains', () => {
  it('gives each flagged drain a one-line reason', () => {
    expect(reasonFor(drain(d14))).toMatch(/same image as after-01.jpg/)
    expect(reasonFor(drain(d6))).toMatch(/goes dark for 4 minutes/)
    expect(reasonFor(drain(d3))).toMatch(/construction debris/)
    expect(reasonFor(drain(d11))).toMatch(/truck rated 10 t/)
    expect(reasonFor(drain(d16))).toMatch(/never enters the approved dump site/)
    expect(reasonFor(drain(d1))).toBe('')
  })

  it('titles a drain whose slip overweighs the truck', () => {
    const facts = caseFacts(drain(d11))
    expect(slipTitle(facts)).toBe('The slip claims more than the truck carries')
    expect(facts.conflictingField).toBe('net')
  })

  it('says one trip, not every trip, when only one missed the dump site', () => {
    const facts = caseFacts(drain(d16))
    expect(facts.allStopTogether).toBe(false)
  })

  it('says plainly when a drain is clean', () => {
    const facts = caseFacts(drain(d1))
    expect(routeTitle(drain(d1), facts)).toBe('Every trace reaches the dump site')
    expect(slipTitle(facts)).toBe('The weighbridge slip')
    expect(photoTitle(facts)).toBe('The photographs')
    expect(facts.r8).toBeNull()
  })
})

describe('line helpers', () => {
  it('slices a line by distance', () => {
    const line: [number, number][] = [[72.0, 19.0], [72.01, 19.0], [72.02, 19.0]]
    const half = sliceLine(line, 0.5)
    expect(half[half.length - 1][0]).toBeCloseTo(72.01, 5)
    expect(lineLength(sliceLine(line, 1))).toBeCloseTo(lineLength(line), 3)
  })
})
