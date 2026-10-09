import type { DrainRow, Summary } from '../types'

export type Kind = 'APPROVE' | 'HOLD'

export interface Split {
  verified: number
  review: number
  held: number
}

/**
 * The same arithmetic as rules.apply_decision on the backend. Approving pays
 * everything; holding keeps what the evidence verified and holds the rest.
 */
export function applyDecision(decision: Kind, evidence: Split): Split {
  if (decision === 'APPROVE') {
    return { verified: evidence.verified + evidence.review + evidence.held, review: 0, held: 0 }
  }
  return { verified: evidence.verified, review: 0, held: evidence.review + evidence.held }
}

export function round(value: number) {
  return Math.round(value * 1000) / 1000
}

export function summarise(drains: DrainRow[], rate: number, claimedTonnes: number): Summary {
  const total = (pick: (row: DrainRow) => number) =>
    round(drains.reduce((sum, row) => sum + pick(row), 0))
  const verified = total((row) => row.verifiedTonnes)
  const review = total((row) => row.reviewTonnes)
  const held = total((row) => row.heldTonnes)

  return {
    ratePerTonne: rate,
    claimedTonnes,
    verifiedTonnes: verified,
    reviewTonnes: review,
    heldTonnes: held,
    claimedRupees: Math.round(claimedTonnes * rate),
    verifiedRupees: Math.round(verified * rate),
    reviewRupees: Math.round(review * rate),
    heldRupees: Math.round(held * rate),
    drainCount: drains.length,
    red: drains.filter((row) => row.verdict === 'RED').length,
    amber: drains.filter((row) => row.verdict === 'AMBER').length,
    green: drains.filter((row) => row.verdict === 'GREEN').length,
    decided: drains.filter((row) => row.decision).length,
  }
}

/**
 * What a decision would do, for the confirmation sheet: the tonnes that move
 * and the whole bill afterwards. It starts from the drain's current row,
 * which is the evidence split until the drain has been decided.
 */
export function previewDecision(
  drains: DrainRow[],
  drainId: string,
  decision: Kind,
  rate: number,
  claimedTonnes: number,
) {
  const row = drains.find((entry) => entry.drainId === drainId)
  const before: Split = row
    ? { verified: row.verifiedTonnes, review: row.reviewTonnes, held: row.heldTonnes }
    : { verified: 0, review: 0, held: 0 }
  const moved = applyDecision(decision, before)

  const rows = drains.map((entry) =>
    entry.drainId === drainId
      ? {
          ...entry,
          decision,
          verifiedTonnes: round(moved.verified),
          reviewTonnes: round(moved.review),
          heldTonnes: round(moved.held),
        }
      : entry,
  )

  const movedTonnes =
    decision === 'APPROVE' ? round(before.review + before.held) : round(before.review)

  return {
    rows,
    movedTonnes,
    movedRupees: Math.round(movedTonnes * rate),
    after: summarise(rows, rate, claimedTonnes),
  }
}
