import { describe, expect, it } from 'vitest'

import { applyDecision, previewDecision, summarise } from '../lib/ledger'
import type { Bill } from '../types'
import snapshot from '../../public/data/demo/bill.json'

// The canonical offline snapshot, so these numbers are the demo's numbers.
const bill = snapshot as unknown as Bill

describe('moving money on a decision', () => {
  it('approving pays everything the evidence split', () => {
    expect(applyDecision('APPROVE', { verified: 60, review: 10, held: 0 })).toEqual({
      verified: 70, review: 0, held: 0,
    })
  })

  it('holding keeps verified tonnes and holds the rest', () => {
    expect(applyDecision('HOLD', { verified: 57, review: 0, held: 28 })).toEqual({
      verified: 57, review: 0, held: 28,
    })
    expect(applyDecision('HOLD', { verified: 0, review: 55, held: 0 })).toEqual({
      verified: 0, review: 0, held: 55,
    })
  })
})

describe('the seeded bill', () => {
  it('summarises to the plan totals', () => {
    const s = summarise(bill.drains, 1800, 1240)
    expect([s.verifiedTonnes, s.reviewTonnes, s.heldTonnes]).toEqual([805, 65, 370])
    expect(s.heldRupees).toBe(666000)
  })

  it('approving both review drains gives 870 t verified, 0 review, 370 t held', () => {
    let rows = bill.drains
    for (const id of ['6', '8']) rows = previewDecision(rows, id, 'APPROVE', 1800, 1240).rows
    const s = summarise(rows, 1800, 1240)
    expect([s.verifiedTonnes, s.reviewTonnes, s.heldTonnes]).toEqual([870, 0, 370])
    expect(s.heldRupees).toBe(666000)
  })

  it('previews what one decision does to the drain and the bill', () => {
    const preview = previewDecision(bill.drains, '14', 'APPROVE', 1800, 1240)
    expect(preview.movedTonnes).toBe(192)
    expect(preview.after.heldTonnes).toBe(178)
    expect(preview.after.verifiedTonnes).toBe(997)
  })
})
