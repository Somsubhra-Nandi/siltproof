import type { Bill, DecisionResult, Drain, DrainRow, Summary } from './types'

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
// Decisions in offline mode are applied here, with the same arithmetic as
// rules.apply_decision on the backend, so the approve-and-release moment
// still works without a deployed API.
function applyDecision(
  decision: 'APPROVE' | 'HOLD',
  evidence: { verified: number; review: number; held: number },
) {
  if (decision === 'APPROVE') {
    return { verified: evidence.verified + evidence.review + evidence.held, review: 0, held: 0 }
  }
  return { verified: evidence.verified, review: 0, held: evidence.review + evidence.held }
}

const offlineEvidence = new Map<string, { verified: number; review: number; held: number }>()
let offlineBill: Bill | null = null

function round(value: number) {
  return Math.round(value * 1000) / 1000
}

function summarise(drains: DrainRow[], rate: number, claimedTonnes: number): Summary {
  const total = (pick: (row: DrainRow) => number) => round(drains.reduce((sum, row) => sum + pick(row), 0))
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
    return offlineBill
  }
  return request<Bill>(`/bill/${billId}`)
}

export async function runVerification(): Promise<Bill> {
  if (offline) {
    // The real call takes a second or two; keep the pause so the button's
    // state is visible rather than flashing past.
    await new Promise((resolve) => setTimeout(resolve, 900))
    return getBill()
  }
  await request(`/verify/${billId}`, { method: 'POST', body: '{}' })
  return getBill()
}

export async function getDrain(drainId: string): Promise<Drain> {
  if (offline) {
    const drain = await snapshot<Drain>(`drain-${drainId}`)
    const bill = await getBill()
    const row = bill.drains.find((entry) => entry.drainId === drainId)
    return row ? { ...drain, decision: row.decision, note: row.note } : drain
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
