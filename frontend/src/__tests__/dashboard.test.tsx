import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../App'
import type { Bill, Drain, DrainRow } from '../types'

// The api module is the seam: these tests drive the screen, not the network.
vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api')
  return {
    ...actual,
    offline: false,
    getBill: vi.fn(),
    runVerification: vi.fn(),
    getDrain: vi.fn(),
    decide: vi.fn(),
    getEvidenceSummary: vi.fn(),
  }
})

import * as api from '../api'

const RULES = {
  R3: 'Photo must not be a reused copy of another photo on this bill',
  R5: 'GPS trace must enter the approved dump site',
  R8: 'Slip time-in must match the GPS arrival at the dump site',
}

const SUMMARY = {
  ratePerTonne: 1800,
  claimedTonnes: 1240,
  verifiedTonnes: 805,
  reviewTonnes: 65,
  heldTonnes: 370,
  claimedRupees: 2232000,
  verifiedRupees: 1449000,
  reviewRupees: 117000,
  heldRupees: 666000,
  drainCount: 18,
  red: 4,
  amber: 2,
  green: 12,
  decided: 0,
}

const ROWS: DrainRow[] = [
  {
    drainId: '14', name: 'Drain section 14', claimedTonnes: 192,
    verifiedTonnes: 0, reviewTonnes: 0, heldTonnes: 192, verdict: 'RED',
    decision: null, note: null, tripCount: 18, failedRules: ['R3'],
  },
  {
    drainId: '6', name: 'Drain section 6', claimedTonnes: 70,
    verifiedTonnes: 60, reviewTonnes: 10, heldTonnes: 0, verdict: 'AMBER',
    decision: null, note: null, tripCount: 7, failedRules: [],
  },
  {
    drainId: '1', name: 'Canal section 1', claimedTonnes: 48,
    verifiedTonnes: 48, reviewTonnes: 0, heldTonnes: 0, verdict: 'GREEN',
    decision: null, note: null, tripCount: 5, failedRules: [],
  },
]

function billFixture(overrides: Partial<Bill> = {}): Bill {
  return {
    billId: 'B1',
    contractor: 'Simulated Contractor Pvt Ltd',
    ward: 'Ward',
    status: 'VERIFIED',
    verifiedAt: '2026-10-08T04:00:00Z',
    verificationMs: 240,
    workWindow: ['2026-09-21T06:00:00+05:30', '2026-10-04T19:00:00+05:30'],
    simulated: true,
    summary: SUMMARY,
    drains: ROWS,
    missingEvidence: null,
    rules: RULES,
    ...overrides,
  }
}

function pendingBill(): Bill {
  return billFixture({
    status: 'PENDING',
    verifiedAt: null,
    drains: ROWS.map((row) => ({ ...row, verdict: null, verifiedTonnes: 0, reviewTonnes: 0, heldTonnes: 0 })),
    summary: {
      ...SUMMARY,
      verifiedTonnes: 0, reviewTonnes: 0, heldTonnes: 0,
      verifiedRupees: 0, reviewRupees: 0, heldRupees: 0,
      red: 0, amber: 0, green: 0,
    },
  })
}

function drainFixture(overrides: Partial<Drain> = {}): Drain {
  return {
    billId: 'B1',
    drainId: '14',
    name: 'Drain section 14',
    verdict: 'RED',
    decision: null,
    note: null,
    decidedAt: null,
    claimedTonnes: 192,
    verifiedTonnes: 0,
    reviewTonnes: 0,
    heldTonnes: 192,
    lengthM: 207.8,
    widthM: 3,
    depthM: 1.8,
    plausibleMaxTonnes: 1885.2,
    claimedRoute: [
      [72.8777, 19.076],
      [72.9318, 19.1272],
    ],
    dumpsite: { name: 'Approved dumping ground', center: [72.9318, 19.1272], geofence: {} },
    findings: [
      {
        rule: 'R3',
        severity: 'hard',
        rule_text: RULES.R3,
        message: 'after-02.jpg is the same image as after-01.jpg, already submitted for drain 9.',
        evidence: {
          s3Key: 'photos/B1/drain14/after-02.jpg',
          original: 'photos/B1/drain9/after-01.jpg',
          originalDrainId: '9',
          hammingDistance: 0,
        },
      },
    ],
    failedRules: ['R3'],
    summary: 'Offline summary, no model was called. The truck never reached the dump site.',
    summaryModelId: null,
    photos: [
      {
        s3Key: 'photos/B1/drain14/after-02.jpg',
        imageUrl: null,
        role: 'after',
        status: 'OK',
        lat: 19.0761,
        lon: 72.8779,
        timestamp: '2026-10-01T15:11:15+05:30',
        hasGps: true,
        pHash: 'e66b145c3a61676a',
        problems: [],
        bedrock: {
          cleared: true, loadType: 'unclear', confidence: 0.9,
          notes: 'Channel bed is visible.', modelId: null, mocked: true, ok: true,
        },
      },
    ],
    trips: [
      {
        tripId: '14#001',
        tripNo: '001',
        vehicleNo: 'MH 01 AA 1000',
        claimedTonnes: 10.6,
        verdict: 'HOLD',
        hardFails: ['R3', 'R5', 'R8'],
        softFails: [],
        findings: [
          {
            rule: 'R5', severity: 'hard', rule_text: RULES.R5,
            message: "The truck's GPS trace never enters the approved dump site.",
            evidence: {},
          },
          {
            rule: 'R8', severity: 'hard', rule_text: RULES.R8,
            message:
              'The slip records time-in at 06:43, 17 minutes before the truck left the drain at 07:00. ' +
              "The GPS trace never reaches the dump site; its last fix, at 07:27, is 44 minutes after the slip's time-in.",
            evidence: {
              timeIn: '06:43',
              arrival: '2026-09-21T07:27:30+05:30',
              arrivalKind: 'lastFix',
              departure: '2026-09-21T07:00:00+05:30',
              minutesEarly: 44.5,
            },
          },
        ],
        startTime: '2026-09-21T07:00:00+05:30',
        arrivalTime: '2026-09-21T07:23:00+05:30',
        slip: {
          s3Key: 'slips/B1/14-001.png',
          ticketNo: 'WB-2026-41910',
          vehicleNo: 'MH 01 AA 1000',
          gross: 23.2,
          tare: 12.6,
          net: 10.6,
          timeIn: '06:43',
          timeOut: '07:09',
          site: 'Ward storm water drain desilting',
          fields: {
            net: { value: 10.6, raw: '10.60 T', confidence: 98.9, ok: true },
            timeIn: { value: '06:43', raw: '06:43', confidence: 96.5, ok: true },
          },
          confidenceAvg: 97.4,
          missingFields: [],
          lowConfidenceFields: [],
        },
        slipImageUrl: null,
        actualRoute: [
          [72.8777, 19.076],
          [72.91, 19.11],
        ],
        actualRouteDistanceM: 5200,
        tracePointCount: 38,
        traceProblem: null,
      },
    ],
    rules: RULES,
    ...overrides,
  }
}

function openOnLoad(drainId: string | null) {
  window.history.replaceState(null, '', drainId ? `/?drain=${drainId}` : '/')
}

beforeEach(() => {
  vi.clearAllMocks()
  openOnLoad(null)
  vi.mocked(api.getBill).mockResolvedValue(billFixture())
  vi.mocked(api.getDrain).mockImplementation(async (id) =>
    id === '6'
      ? drainFixture({
          drainId: '6', verdict: 'AMBER', findings: [], failedRules: [], heldTonnes: 0, reviewTonnes: 10,
          trips: [{
            ...drainFixture().trips[0], tripId: '6#006', verdict: 'REVIEW', hardFails: [], softFails: ['GPS_GAP'],
            findings: [{ rule: 'GPS_GAP', severity: 'soft', rule_text: '', message: 'The GPS trace goes dark for 4 minutes.', evidence: {} }],
          }],
        })
      : drainFixture(),
  )
  vi.mocked(api.runVerification).mockResolvedValue(billFixture())
})

afterEach(() => openOnLoad(null))

const amount = (container: HTMLElement) =>
  container.querySelector('.figure .amount')?.textContent ?? ''

// ------------------------------------------------------------ overview
describe('before verification', () => {
  beforeEach(() => vi.mocked(api.getBill).mockResolvedValue(pendingBill()))

  it("shows only the contractor's claim, and no verdict", async () => {
    const { container } = render(<App />)

    expect(await screen.findByRole('button', { name: /run verification/i })).toBeEnabled()
    expect(amount(container)).toBe('₹22.32lakh')
    expect(screen.getAllByText('Not yet calculated')).toHaveLength(3)
    expect(container.querySelector('.stamp')).toBeNull()
    expect(screen.getByText(/Simulated demonstration/)).toBeInTheDocument()
    expect(screen.getByText(/18 drains|3 drains on this bill, not checked/)).toBeInTheDocument()
  })

  it('goes from an unchecked bill to a verified one when the button is pressed', async () => {
    const { container } = render(<App />)

    fireEvent.click(await screen.findByRole('button', { name: /^run verification$/i }))

    await waitFor(() => expect(amount(container)).toBe('₹6.66lakh'))
    expect(screen.getByText('805 t')).toBeInTheDocument()
    expect(screen.getByText('65 t')).toBeInTheDocument()
    expect(container.querySelector('.figure .stamp.held')?.textContent).toContain('370 t')
    expect(screen.getByRole('button', { name: /run again/i })).toBeInTheDocument()
  })

  it('will not let the engineer decide before verification', async () => {
    openOnLoad('14')
    vi.mocked(api.getDrain).mockResolvedValue(drainFixture({ verdict: null }))
    render(<App />)

    expect(await screen.findByText('Run verification before deciding.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /approve/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /hold/i })).toBeDisabled()
  })
})

describe('the verified overview', () => {
  it('leads with the held money and a HELD stamp', async () => {
    const { container } = render(<App />)

    await waitFor(() => expect(amount(container)).toBe('₹6.66lakh'))
    expect(screen.getByText('Payment held by the evidence')).toBeInTheDocument()
    expect(screen.getByText('1,240 t, ₹22.32 L')).toBeInTheDocument()
  })

  it('lists flagged drains with stamps and a reason from the first finding', async () => {
    render(<App />)

    expect(await screen.findByText(/same image as after-01.jpg/)).toBeInTheDocument()
    expect(await screen.findByText('The GPS trace goes dark for 4 minutes.')).toBeInTheDocument()
    expect(screen.getByText('Held 192 of 192 t')).toBeInTheDocument()
    expect(screen.getByText('10 of 70 t for your review')).toBeInTheDocument()
    expect(screen.getByText(/1 drains pass every check: 1\./)).toBeInTheDocument()
  })

  it('surfaces an API failure instead of showing nothing', async () => {
    vi.mocked(api.getBill).mockRejectedValue(new Error('No bill B1. Has the seed run?'))
    render(<App />)
    expect(await screen.findByText(/Has the seed run/)).toBeInTheDocument()
  })

  it('warns when evidence is missing', async () => {
    vi.mocked(api.getBill).mockResolvedValue(
      billFixture({
        missingEvidence: {
          tripsWithoutSlip: 2, tripsWithoutTrace: 0, unreadableTraces: 1,
          evidenceErrors: 0, drainsWithoutPhotos: 0,
        },
      }),
    )
    render(<App />)
    expect(await screen.findByText(/Evidence gaps/)).toBeInTheDocument()
    expect(screen.getByText(/in review, not held/)).toBeInTheDocument()
  })

  it('approves a review drain inline, after a confirmation, and updates the money', async () => {
    vi.mocked(api.decide).mockResolvedValue({
      drainId: '6',
      decision: 'APPROVE',
      note: null,
      summary: { ...SUMMARY, verifiedTonnes: 815, reviewTonnes: 55, verifiedRupees: 1467000, reviewRupees: 99000, decided: 1 },
      drains: ROWS.map((row) =>
        row.drainId === '6' ? { ...row, decision: 'APPROVE', verifiedTonnes: 70, reviewTonnes: 0 } : row,
      ),
    })
    render(<App />)

    fireEvent.click(await screen.findByText('10 of 70 t for your review'))
    fireEvent.click(screen.getByRole('button', { name: 'Approve ₹18,000' }))

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Approve 10 t on drain 6?')).toBeInTheDocument()
    // The preview runs the same arithmetic over the bill's rows: 0 + 70 + 48.
    expect(within(dialog).getByText('118 t')).toBeInTheDocument()
    expect(api.decide).not.toHaveBeenCalled()

    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm approval' }))

    await waitFor(() => expect(api.decide).toHaveBeenCalledWith('6', 'APPROVE', ''))
    expect(await screen.findByText('815 t')).toBeInTheDocument()
    expect(await screen.findByText(/Approved by you/)).toBeInTheDocument()
  })
})

// ------------------------------------------------------------ case file
describe('the case file', () => {
  beforeEach(() => openOnLoad('14'))

  it('lays out the three exhibits from the findings', async () => {
    render(<App />)

    expect(await screen.findByText('Trip 001 never reached the dump site')).toBeInTheDocument()
    expect(screen.getByText('The slip is stamped before the truck left')).toBeInTheDocument()
    expect(screen.getByText('The after-photo was filed for drain 9 first')).toBeInTheDocument()
    expect(screen.getAllByText(/same image as after-01.jpg/).length).toBeGreaterThan(0)
    expect(screen.getByText(/17 minutes before the truck left/)).toBeInTheDocument()
  })

  it("keeps R8's slip time-in, departure and last fix apart, and never shows an arrival", async () => {
    const { container } = render(<App />)
    await screen.findByText('Trip 001 never reached the dump site')

    const labels = [...container.querySelectorAll('.ev-label')].map((el) => el.textContent)
    expect(labels).toEqual(['06:43time in', '07:09time out', '07:00leaves drain 14', '07:23stops 2.2 km short', '07:27last fix'].map((t) => expect.stringMatching(new RegExp(`^${t.slice(0, 5)}`))))
    expect(labels.join(' ')).not.toMatch(/arrive/)
    expect(screen.getByText('Never arrives')).toBeInTheDocument()
    expect(screen.getByText(/R8: slip time-in against the last GPS fix, 44 min apart/)).toBeInTheDocument()
  })

  it('highlights the slip field the rule is arguing with', async () => {
    const { container } = render(<App />)
    await screen.findByText('Trip 001 never reached the dump site')
    expect(container.querySelector('.fields .hit')?.textContent).toContain('06:43')
  })

  it('keeps rule findings and AI observations apart', async () => {
    const { container } = render(<App />)
    await screen.findByText('Trip 001 never reached the dump site')

    expect(screen.getByText('Rule R3, deterministic')).toBeInTheDocument()
    const ai = container.querySelector('.ai')!
    expect(within(ai as HTMLElement).getByText('AI observation, not a finding')).toBeInTheDocument()
    expect(within(ai as HTMLElement).getByText(/no model was called/)).toBeInTheDocument()
    expect(screen.getByText(/Evidence summary, offline/)).toBeInTheDocument()
  })

  it('shows the labelled photo slot when there is no image link', async () => {
    render(<App />)
    await screen.findByText('Trip 001 never reached the dump site')
    expect(screen.getAllByText(/Not yet supplied/).length).toBe(2)
    expect(screen.getByText('No slip image here')).toBeInTheDocument()
  })

  it('confirms a hold and passes the note along', async () => {
    vi.mocked(api.decide).mockResolvedValue({
      drainId: '14', decision: 'HOLD', note: 'Referred to vigilance.',
      summary: { ...SUMMARY, decided: 1 },
      drains: ROWS.map((row) => (row.drainId === '14' ? { ...row, decision: 'HOLD', note: 'Referred to vigilance.' } : row)),
    })
    render(<App />)

    fireEvent.change(await screen.findByLabelText('Note for the file'), {
      target: { value: 'Referred to vigilance.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Hold ₹3,45,600' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Hold ₹3,45,600 on drain 14?')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm hold' }))

    await waitFor(() => expect(api.decide).toHaveBeenCalledWith('14', 'HOLD', 'Referred to vigilance.'))
    expect(await screen.findByText('You held ₹3,45,600 on drain 14.')).toBeInTheDocument()
    expect(screen.getByText('Note: Referred to vigilance.')).toBeInTheDocument()
    expect(await screen.findByRole('status')).toHaveTextContent(/Decision saved/)
  })

  it('needs a site note before approving against hard findings', async () => {
    render(<App />)

    fireEvent.click(await screen.findByRole('button', { name: 'Approve and release' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/despite three failed hard checks/)).toBeInTheDocument()
    const confirm = within(dialog).getByRole('button', { name: 'Approve and release' })
    expect(confirm).toBeDisabled()

    fireEvent.change(within(dialog).getByLabelText(/Site note/), { target: { value: 'Too short' } })
    expect(confirm).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText(/Site note/), {
      target: { value: 'Inspected on 2 Oct; the section is clear of silt.' },
    })
    expect(confirm).toBeEnabled()
  })

  it('closes the confirmation on Escape without deciding', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Hold ₹3,45,600' }))
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(api.decide).not.toHaveBeenCalled()
  })

  it('says the decision was not saved when the service fails, and leaves the bill alone', async () => {
    vi.mocked(api.decide).mockRejectedValue(new Error('The bill service did not answer.'))
    const { container } = render(<App />)

    fireEvent.click(await screen.findByRole('button', { name: 'Hold ₹3,45,600' }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Confirm hold' }))

    expect(await screen.findByText('Not saved.')).toBeInTheDocument()
    expect(screen.getByText(/did not answer/)).toBeInTheDocument()
    expect(container.querySelector('.billbox')?.textContent).toContain('₹6.66 L')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('shows a decision once it has been taken', async () => {
    vi.mocked(api.getBill).mockResolvedValue(
      billFixture({
        drains: ROWS.map((row) => (row.drainId === '14' ? { ...row, decision: 'HOLD', note: 'Truck never arrived.' } : row)),
      }),
    )
    vi.mocked(api.getDrain).mockResolvedValue(drainFixture({ decision: 'HOLD', note: 'Truck never arrived.' }))
    render(<App />)

    expect(await screen.findByText('You held ₹3,45,600 on drain 14.')).toBeInTheDocument()
    expect(screen.getByText('Note: Truck never arrived.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Change decision' })).toBeInTheDocument()
  })

  it('goes back to all drains', async () => {
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: /All 3 drains/ }))
    await waitFor(() => expect(screen.queryByText('Trip 001 never reached the dump site')).toBeNull())
    expect(window.location.search).toBe('')
  })
})

describe('reduced motion', () => {
  const original = window.matchMedia
  afterEach(() => {
    window.matchMedia = original
  })

  it('jumps straight to the end state of a verification', async () => {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: query.includes('reduce'),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })) as unknown as typeof window.matchMedia
    vi.mocked(api.getBill).mockResolvedValue(pendingBill())
    const { container } = render(<App />)

    const button = await screen.findByRole('button', { name: /^run verification$/i })
    await act(async () => {
      fireEvent.click(button)
    })

    await waitFor(() => expect(amount(container)).toBe('₹6.66lakh'))
    expect(container.querySelector('.stamp.slam')).toBeNull()
    expect(container.querySelector('.row.enter')).toBeNull()
  })
})
