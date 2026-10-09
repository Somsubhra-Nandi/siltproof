// VITE_BILL_SOURCE: the investigation from the committed snapshot or the live
// bill, with browser-only decisions whenever nothing may be saved, and the
// judge trial always on VITE_API_BASE_URL.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'

const DEMO = resolve(__dirname, '../../public/data/demo')
const API = 'https://api.example.test'
const snapshotFile = (name: string) => JSON.parse(readFileSync(resolve(DEMO, `${name}.json`), 'utf8'))

type Call = { url: string; method: string }

function stubFetch(apiReply: (url: string, method: string) => [number, unknown]) {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    calls.push({ url, method })
    const local = url.match(/^\/data\/demo\/(.+)\.json$/)
    const [status, body] = local ? [200, snapshotFile(local[1])] : apiReply(url, method)
    return new Response(JSON.stringify(body), { status })
  }))
  return calls
}

async function load(env: Record<string, string>) {
  vi.resetModules()
  for (const [name, value] of Object.entries(env)) vi.stubEnv(name, value)
  return import('../api')
}

afterEach(() => {
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
})

async function approveReviewDrains(api: Awaited<ReturnType<typeof load>>) {
  await api.runVerification()
  await api.decide('6', 'APPROVE', '')
  return api.decide('8', 'APPROVE', '')
}

describe('VITE_BILL_SOURCE', () => {
  it('snapshot with an API configured never sends the bill to the API, and decides in the browser', async () => {
    const calls = stubFetch(() => [500, { error: 'the bill must not be fetched from the API' }])
    const api = await load({ VITE_API_BASE_URL: API, VITE_BILL_SOURCE: 'snapshot' })
    expect(api.billSource).toBe('snapshot')

    const pending = await api.getBill()
    expect(pending.drains).toHaveLength(18)
    expect(pending.summary.heldTonnes).toBe(0)

    const verified = await api.runVerification()
    expect(verified.summary).toMatchObject({ claimedTonnes: 1240, verifiedTonnes: 805, reviewTonnes: 65, heldTonnes: 370 })
    expect(verified.summary.heldRupees).toBe(666000)

    const result = await approveReviewDrains(api)
    expect(result.local).toBe(true)
    expect(result.summary).toMatchObject({ verifiedTonnes: 870, reviewTonnes: 0, heldTonnes: 370 })
    expect(calls.every((call) => !call.url.startsWith(API))).toBe(true)
  })

  it('drain 14 from the snapshot carries its own evidence', async () => {
    stubFetch(() => [500, {}])
    const api = await load({ VITE_API_BASE_URL: API, VITE_BILL_SOURCE: 'snapshot' })
    await api.runVerification()
    const drain = await api.getDrain('14')
    expect(drain.drainId).toBe('14')
    expect(drain.trips).toHaveLength(18)
    expect(drain.failedRules).toContain('R3')
    const tripRules = new Set(drain.trips.flatMap((trip) => trip.hardFails))
    expect([...tripRules]).toEqual(expect.arrayContaining(['R5', 'R8']))
  })

  it('api on a read-only bill shows the stored verdicts and keeps decisions in the browser', async () => {
    const bill = snapshotFile('bill')
    const calls = stubFetch((url, method) => {
      if (url === `${API}/bill/B1`) return [200, bill]
      if (method === 'POST') return [403, { error: 'read-only', code: 'READ_ONLY' }]
      return [404, { error: 'unexpected' }]
    })
    const api = await load({ VITE_API_BASE_URL: API, VITE_BILL_SOURCE: 'api' })
    expect(api.billSource).toBe('api')
    expect((await api.getBill()).summary.heldTonnes).toBe(0)   // not yet checked

    const result = await approveReviewDrains(api)
    expect(result.local).toBe(true)
    expect(result.summary).toMatchObject({ verifiedTonnes: 870, reviewTonnes: 0, heldTonnes: 370 })
    expect(api.decisionsAreLocal()).toBe(true)
    // The refused verify marks the bill read-only, so no decision ever leaves the browser.
    expect(calls.filter((call) => call.method === 'POST').map((call) => call.url))
      .toEqual([`${API}/verify/B1`])
  })

  it('api on a writable bill saves decisions to the server', async () => {
    const bill = snapshotFile('bill')
    stubFetch((url, method) => {
      if (url === `${API}/bill/B1`) return [200, bill]
      if (url === `${API}/verify/B1`) return [200, {}]
      if (url === `${API}/decision` && method === 'POST')
        return [200, { drainId: '6', decision: 'APPROVE', note: null, summary: bill.summary, drains: bill.drains }]
      return [404, {}]
    })
    const api = await load({ VITE_API_BASE_URL: API, VITE_BILL_SOURCE: 'api' })
    await api.runVerification()
    const result = await api.decide('6', 'APPROVE', '')
    expect(result.local).toBeUndefined()
    expect(api.decisionsAreLocal()).toBe(false)
  })

  it('the judge trial uses the API whatever the bill source', async () => {
    vi.resetModules()
    vi.stubEnv('VITE_API_BASE_URL', API)
    vi.stubEnv('VITE_BILL_SOURCE', 'snapshot')
    const trial = await import('../features/judge-trial/api')
    expect(trial.apiBase()).toBe(API)
    expect(trial.trialsAvailable()).toBe(true)
  })
})
