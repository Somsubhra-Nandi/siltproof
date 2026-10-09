import { applyDecision, round, summarise } from './lib/ledger'
import type { Bill, DecisionResult, Drain } from './types'

const base = (import.meta.env.VITE_API_BASE_URL ?? '').trim().replace(/\/$/, '')
const billId = (import.meta.env.VITE_BILL_ID ?? 'B1').trim()

/**
 * With no API URL configured the app runs off the snapshot in
 * public/data/demo, written by scripts/make_demo_fixtures.py. That keeps the
 * screen clickable with no AWS account, and leaves a fallback if the stack is
 * unreachable while recording.
 */
export const offline = base === ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })

  const text = await response.text()
  const payload = text ? JSON.parse(text) : {}

  if (!response.ok) {
    throw new Error(payload.error ?? `${response.status} ${response.statusText}`)
  }
  return payload as T
}

async function snapshot<T>(name: string): Promise<T> {
  const response = await fetch(`/data/demo/${name}.json`)
  if (!response.ok) {
    throw new Error(
      `No demo snapshot (${name}). Set VITE_API_BASE_URL, or run ` +
        `python scripts/make_demo_fixtures.py`,
    )
  }
  return (await response.json()) as T
}

// ---------------------------------------------------------------- offline
// Decisions in offline mode are applied with lib/ledger's applyDecision, the
// same arithmetic as rules.apply_decision on the backend, so the
// approve-and-release moment still works without a deployed API.
const offlineEvidence = new Map<string, { verified: number; review: number; held: number }>()
let offlineBill: Bill | null = null

/**
 * Offline, the snapshot is already verified, but the demo has to start where
 * the engineer starts: a bill nobody has checked yet. So the snapshot is
 * served through a pending projection until Run Verification is pressed.
 *
 * ?state=verified skips that, for when you are working on the verified view.
 */
let offlineVerified =
  typeof window !== 'undefined' &&
  new URLSearchParams(window.location.search).get('state') === 'verified'

export function pendingView(bill: Bill): Bill {
  return {
    ...bill,
    status: 'PENDING',
    verifiedAt: null,
    verificationMs: undefined,
    missingEvidence: null,
    drains: bill.drains.map((row) => ({
      ...row,
      verdict: null,
      decision: null,
      note: null,
      verifiedTonnes: 0,
      reviewTonnes: 0,
      heldTonnes: 0,
      failedRules: [],
    })),
    summary: {
      ...bill.summary,
      verifiedTonnes: 0,
      reviewTonnes: 0,
      heldTonnes: 0,
      verifiedRupees: 0,
      reviewRupees: 0,
      heldRupees: 0,
      red: 0,
      amber: 0,
      green: 0,
      decided: 0,
      pendingTonnes: bill.summary.claimedTonnes,
    },
  }
}

function pendingDrain(drain: Drain): Drain {
  return {
    ...drain,
    verdict: null,
    decision: null,
    note: null,
    verifiedTonnes: 0,
    reviewTonnes: 0,
    heldTonnes: 0,
    findings: [],
    failedRules: [],
    summary: null,
    trips: drain.trips.map((trip) => ({
      ...trip,
      verdict: null,
      hardFails: [],
      softFails: [],
      findings: [],
    })),
  }
}

// ------------------------------------------------------------------- api
export async function getBill(): Promise<Bill> {
  if (offline) {
    if (!offlineBill) {
      offlineBill = await snapshot<Bill>('bill')
      for (const row of offlineBill.drains) {
        offlineEvidence.set(row.drainId, {
          verified: row.verifiedTonnes,
          review: row.reviewTonnes,
          held: row.heldTonnes,
        })
      }
    }
    return offlineVerified ? offlineBill : pendingView(offlineBill)
  }
  return request<Bill>(`/bill/${billId}`)
}

export async function runVerification(): Promise<Bill> {
  if (offline) {
    // The real call takes a second or two; keep the pause so the button's
    // state is visible rather than flashing past.
    await new Promise((resolve) => setTimeout(resolve, 900))
    offlineVerified = true
    return getBill()
  }
  await request(`/verify/${billId}`, { method: 'POST', body: '{}' })
  return getBill()
}

export async function getDrain(drainId: string): Promise<Drain> {
  if (offline) {
    const drain = await snapshot<Drain>(`drain-${drainId}`)
    if (!offlineVerified) return pendingDrain(drain)

    const bill = await getBill()
    const row = bill.drains.find((entry) => entry.drainId === drainId)
    return row
      ? {
          ...drain,
          decision: row.decision,
          note: row.note,
          verifiedTonnes: row.verifiedTonnes,
          reviewTonnes: row.reviewTonnes,
          heldTonnes: row.heldTonnes,
        }
      : drain
  }
  return request<Drain>(`/drain/${drainId}?billId=${billId}`)
}

export async function decide(
  drainId: string,
  decision: 'APPROVE' | 'HOLD',
  note: string,
): Promise<DecisionResult> {
  if (offline) {
    const bill = await getBill()
    const drains = bill.drains.map((row) => {
      if (row.drainId !== drainId) return row
      const evidence = offlineEvidence.get(drainId) ?? {
        verified: row.verifiedTonnes,
        review: row.reviewTonnes,
        held: row.heldTonnes,
      }
      const moved = applyDecision(decision, evidence)
      return {
        ...row,
        decision,
        note: note.trim() || null,
        verifiedTonnes: round(moved.verified),
        reviewTonnes: round(moved.review),
        heldTonnes: round(moved.held),
      }
    })

    const summary = summarise(drains, bill.summary.ratePerTonne, bill.summary.claimedTonnes)
    offlineBill = { ...bill, drains, summary }

    return { drainId, decision, note: note.trim() || null, summary, drains }
  }

  return request<DecisionResult>('/decision', {
    method: 'POST',
    body: JSON.stringify({ billId, drainId, decision, note }),
  })
}

export async function getEvidenceSummary(
  drainId: string,
): Promise<{ summary: string; cached: boolean; modelId: string | null }> {
  if (offline) {
    const drain = await snapshot<Drain>(`drain-${drainId}`)
    return {
      summary:
        drain.summary ??
        'No cached evidence summary in the offline snapshot. Connect the API to ask Bedrock.',
      cached: true,
      modelId: null,
    }
  }

  return request(`/drain/${drainId}/summary`, {
    method: 'POST',
    body: JSON.stringify({ billId }),
  })
}


/** Tests only: forget the cached snapshot and the verified flag. */
export function __resetOfflineState(verified = false) {
  offlineBill = null
  offlineEvidence.clear()
  offlineVerified = verified
}
