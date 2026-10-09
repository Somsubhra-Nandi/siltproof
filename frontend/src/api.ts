import { applyDecision, round, summarise } from './lib/ledger'
import type { Bill, DecisionResult, Drain } from './types'

const base = (import.meta.env.VITE_API_BASE_URL ?? '').trim().replace(/\/$/, '')
const billId = (import.meta.env.VITE_BILL_ID ?? 'B1').trim()
const requestedSource = (import.meta.env.VITE_BILL_SOURCE ?? '').trim().toLowerCase()

/**
 * Where the 18-drain investigation comes from (VITE_BILL_SOURCE):
 *
 * - `snapshot`: the committed simulation in public/data/demo, written by
 *   scripts/make_demo_fixtures.py. Decisions are applied in this browser only.
 * - `api`: the live bill at VITE_API_BASE_URL.
 * - unset: `api` when VITE_API_BASE_URL is set, otherwise `snapshot`.
 *
 * The judge trial (trial.html) ignores this and always uses VITE_API_BASE_URL,
 * so the public site can show the prepared investigation from the snapshot
 * while trials run against the real API.
 */
export const billSource: 'snapshot' | 'api' =
  base === '' || requestedSource === 'snapshot' ? 'snapshot' : 'api'
export const offline = billSource === 'snapshot'

class ApiError extends Error {
  status: number
  code?: string

  constructor(message: string, status: number, code?: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })

  const text = await response.text()
  const payload = text ? JSON.parse(text) : {}

  if (!response.ok) {
    throw new ApiError(payload.error ?? `${response.status} ${response.statusText}`, response.status, payload.code)
  }
  return payload as T
}

const readOnly = (cause: unknown) => cause instanceof ApiError && cause.code === 'READ_ONLY'

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

// ------------------------------------------------- browser-only decisions
// Decisions on the snapshot, or on a live bill the server keeps read-only
// (403 READ_ONLY), are applied here with lib/ledger's applyDecision, the same
// arithmetic as rules.apply_decision on the backend. They are never sent to
// AWS, and every result says so (`local: true`).
const evidence = new Map<string, { verified: number; review: number; held: number }>()
let sheet: Bill | null = null
let localDecisions = offline

/** True when decisions are applied in this browser rather than saved. */
export const decisionsAreLocal = () => localDecisions

/**
 * The demo starts where the engineer starts: a bill nobody has checked yet.
 * Whatever the source, the verified bill is served through a pending
 * projection until Run Verification is pressed in this session.
 *
 * ?state=verified skips that, for when you are working on the verified view.
 */
let verifiedThisSession =
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
function remember(bill: Bill) {
  if (evidence.size > 0) return
  for (const row of bill.drains) {
    evidence.set(row.drainId, {
      verified: row.verifiedTonnes,
      review: row.reviewTonnes,
      held: row.heldTonnes,
    })
  }
}

/** The verified bill from its source, with this browser's decisions on it. */
async function loadBill(): Promise<Bill> {
  if (sheet && (offline || localDecisions)) return sheet
  const bill = offline ? await snapshot<Bill>('bill') : await request<Bill>(`/bill/${billId}`)
  remember(bill)
  sheet = bill
  return bill
}

export async function getBill(): Promise<Bill> {
  const bill = await loadBill()
  return verifiedThisSession ? bill : pendingView(bill)
}

export async function runVerification(): Promise<Bill> {
  if (offline) {
    // The real call takes a second or two; keep the pause so the button's
    // state is visible rather than flashing past.
    await new Promise((resolve) => setTimeout(resolve, 900))
  } else {
    try {
      await request(`/verify/${billId}`, { method: 'POST', body: '{}' })
      sheet = null
    } catch (cause) {
      // A read-only bill was verified when it was seeded; show that result.
      if (!readOnly(cause)) throw cause
      localDecisions = true
    }
  }
  verifiedThisSession = true
  return getBill()
}

export async function getDrain(drainId: string): Promise<Drain> {
  const drain = offline
    ? await snapshot<Drain>(`drain-${drainId}`)
    : await request<Drain>(`/drain/${drainId}?billId=${billId}`)
  if (!verifiedThisSession) return pendingDrain(drain)
  if (!localDecisions) return drain

  const bill = await loadBill()
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

export async function decide(
  drainId: string,
  decision: 'APPROVE' | 'HOLD',
  note: string,
): Promise<DecisionResult> {
  if (!localDecisions) {
    try {
      return await request<DecisionResult>('/decision', {
        method: 'POST',
        body: JSON.stringify({ billId, drainId, decision, note }),
      })
    } catch (cause) {
      if (!readOnly(cause)) throw cause
      localDecisions = true
    }
  }

  const bill = await loadBill()
  const drains = bill.drains.map((row) => {
    if (row.drainId !== drainId) return row
    const split = evidence.get(drainId) ?? {
      verified: row.verifiedTonnes,
      review: row.reviewTonnes,
      held: row.heldTonnes,
    }
    const moved = applyDecision(decision, split)
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
  sheet = { ...bill, drains, summary }

  return { drainId, decision, note: note.trim() || null, summary, drains, local: true }
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


/** Tests only: forget the cached bill, local decisions and the verified flag. */
export function __resetOfflineState(verified = false) {
  sheet = null
  evidence.clear()
  localDecisions = offline
  verifiedThisSession = verified
}
