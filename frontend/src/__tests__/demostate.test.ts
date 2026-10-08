import { describe, expect, it } from 'vitest'

import { pendingView } from '../api'
import type { Bill } from '../types'

/**
 * The offline snapshot is already verified, but the demo has to open where
 * the engineer opens: a bill nobody has checked. These cover the projection
 * that makes that true without a second set of fixtures.
 */
const VERIFIED: Bill = {
  billId: 'B1',
  contractor: 'Simulated Contractor Pvt Ltd',
  ward: 'Ward',
  status: 'VERIFIED',
  verifiedAt: '2026-10-08T04:00:00Z',
  verificationMs: 240,
  workWindow: ['2026-09-21T06:00:00+05:30', '2026-10-04T19:00:00+05:30'],
  simulated: true,
  summary: {
    ratePerTonne: 1800,
    claimedTonnes: 1240, verifiedTonnes: 805, reviewTonnes: 65, heldTonnes: 370,
    claimedRupees: 2232000, verifiedRupees: 1449000, reviewRupees: 117000,
    heldRupees: 666000,
    drainCount: 18, red: 4, amber: 2, green: 12, decided: 0,
  },
  drains: [
    {
      drainId: '14', name: 'Drain section 14', claimedTonnes: 192,
      verifiedTonnes: 0, reviewTonnes: 0, heldTonnes: 192, verdict: 'RED',
      decision: null, note: null, tripCount: 18, failedRules: ['R3'],
    },
    {
      drainId: '1', name: 'Drain section 1', claimedTonnes: 48,
      verifiedTonnes: 48, reviewTonnes: 0, heldTonnes: 0, verdict: 'GREEN',
      decision: 'APPROVE', note: 'checked', tripCount: 5, failedRules: [],
    },
  ],
  missingEvidence: {
    tripsWithoutSlip: 0, tripsWithoutTrace: 0, unreadableTraces: 0,
    evidenceErrors: 0, drainsWithoutPhotos: 0,
  },
  rules: { R3: 'Photo must not be a reused copy' },
}

describe('the pre-verification view of a verified snapshot', () => {
  const pending = pendingView(VERIFIED)

  it('reports the bill as not yet verified', () => {
    expect(pending.status).toBe('PENDING')
    expect(pending.verifiedAt).toBeNull()
  })

  it('keeps the claim, which is the one number known before any checking', () => {
    expect(pending.summary.claimedTonnes).toBe(1240)
    expect(pending.summary.claimedRupees).toBe(2232000)
    expect(pending.summary.pendingTonnes).toBe(1240)
  })

  it('holds back every verdict figure', () => {
    expect(pending.summary.verifiedTonnes).toBe(0)
    expect(pending.summary.reviewTonnes).toBe(0)
    expect(pending.summary.heldTonnes).toBe(0)
    expect(pending.summary.heldRupees).toBe(0)
    expect([pending.summary.red, pending.summary.amber, pending.summary.green]).toEqual([0, 0, 0])
  })

  it('leaves every drain grey and undecided', () => {
    for (const drain of pending.drains) {
      expect(drain.verdict).toBeNull()
      expect(drain.decision).toBeNull()
      expect(drain.note).toBeNull()
      expect(drain.failedRules).toEqual([])
      expect(drain.heldTonnes).toBe(0)
    }
  })

  it("keeps each drain's claim, so the map still has sections to show", () => {
    expect(pending.drains.map((row) => row.claimedTonnes)).toEqual([192, 48])
    expect(pending.drains).toHaveLength(VERIFIED.drains.length)
  })

  it('does not alter the verified bill it was given', () => {
    expect(VERIFIED.status).toBe('VERIFIED')
    expect(VERIFIED.summary.heldTonnes).toBe(370)
    expect(VERIFIED.drains[0].verdict).toBe('RED')
  })
})
