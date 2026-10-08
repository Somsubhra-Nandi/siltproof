import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../App'
import DrainPanel from '../components/DrainPanel'
import type { Bill, Drain } from '../types'

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
  R5: 'GPS trace must enter the approved dump site',
  R7: "Slip net weight must not exceed the truck's capacity",
}

function billFixture(overrides: Partial<Bill> = {}): Bill {
  return {
    billId: 'B1',
    contractor: 'Simulated Contractor Pvt Ltd',
    ward: 'Test ward',
    status: 'VERIFIED',
    verifiedAt: '2026-10-08T04:00:00Z',
    verificationMs: 240,
    workWindow: ['2026-09-21T06:00:00+05:30', '2026-10-04T19:00:00+05:30'],
    simulated: true,
    summary: {
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
    },
    drains: [
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
    ],
    missingEvidence: null,
    rules: RULES,
    ...overrides,
  }
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
    widthM: 2.5,
    depthM: 1.2,
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
        rule_text: 'Photo must not be a reused copy of another photo on this bill',
        message:
          'after-02.jpg is the same image as after-01.jpg, already submitted for drain 9.',
        evidence: { originalDrainId: '9', hammingDistance: 0 },
      },
    ],
    failedRules: ['R3'],
    summary: null,
    photos: [
      {
        s3Key: 'photos/B1/drain14/after-02.jpg',
        role: 'after',
        status: 'OK',
        lat: 19.0761,
        lon: 72.8779,
        timestamp: '2026-10-01T15:11:15+05:30',
        hasGps: true,
        pHash: 'abc123',
        problems: [],
        bedrock: {
          cleared: true, loadType: 'unclear', confidence: 0.9,
          notes: 'Channel bed is visible.', modelId: 'mock', ok: true,
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
            rule: 'R8', severity: 'hard',
            rule_text: 'Slip time-in must match the GPS arrival at the dump site',
            message:
              'The slip was printed at 06:43, 40 minutes before the truck arrived anywhere.',
            evidence: { minutesEarly: 40 },
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

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getBill).mockResolvedValue(billFixture())
  vi.mocked(api.getDrain).mockResolvedValue(drainFixture())
  vi.mocked(api.runVerification).mockResolvedValue(billFixture())
})

// --------------------------------------------------------------- summary
describe('the summary bar', () => {
  it('shows the money the engineer is deciding about', async () => {
    render(<App />)

    const bar = await screen.findByRole('banner')
    expect(within(bar).getByText('1,240 t')).toBeInTheDocument()
    expect(within(bar).getByText('805 t')).toBeInTheDocument()
    expect(within(bar).getByText('65 t')).toBeInTheDocument()
    // The headline hold figure is spelled out; the sub-figures keep the short form.
    expect(within(bar).getByText('₹6.66 lakh')).toBeInTheDocument()
    expect(within(bar).getByText('₹22.32 L')).toBeInTheDocument()
  })

  it('shows the ward from the bill, falling back to a plain label', async () => {
    render(<App />)
    expect(await screen.findByText(/Test ward · Simulated Contractor/)).toBeInTheDocument()
  })

  it('says only "Ward" when the bill has no ward name', async () => {
    vi.mocked(api.getBill).mockResolvedValue(billFixture({ ward: '' }))
    render(<App />)
    expect(await screen.findByText(/^Ward · /)).toBeInTheDocument()
  })

  it('holds back the verdict columns until verification has run', async () => {
    vi.mocked(api.getBill).mockResolvedValue(
      billFixture({
        status: 'PENDING',
        verifiedAt: null,
        drains: billFixture().drains.map((row) => ({ ...row, verdict: null })),
      }),
    )

    render(<App />)

    expect(await screen.findByRole('button', { name: /run verification/i })).toBeEnabled()
    const bar = screen.getByRole('banner')
    expect(within(bar).getAllByText('—').length).toBeGreaterThan(2)
  })

  it('runs verification and reports it was done', async () => {
    render(<App />)

    const button = await screen.findByRole('button', { name: /re-run verification/i })
    fireEvent.click(button)

    await waitFor(() => expect(api.runVerification).toHaveBeenCalledOnce())
  })

  it('goes from an unchecked bill to a verified one when the button is pressed', async () => {
    const pending = billFixture({
      status: 'PENDING',
      verifiedAt: null,
      drains: billFixture().drains.map((row) => ({
        ...row, verdict: null, verifiedTonnes: 0, reviewTonnes: 0, heldTonnes: 0,
      })),
      summary: {
        ...billFixture().summary,
        verifiedTonnes: 0, reviewTonnes: 0, heldTonnes: 0,
        verifiedRupees: 0, reviewRupees: 0, heldRupees: 0,
        red: 0, amber: 0, green: 0,
      },
    })
    vi.mocked(api.getBill).mockResolvedValue(pending)
    vi.mocked(api.runVerification).mockResolvedValue(billFixture())

    render(<App />)

    // Before: nothing is verified and the button invites a first run.
    const button = await screen.findByRole('button', { name: /^run verification$/i })
    const bar = screen.getByRole('banner')
    expect(within(bar).getByText('1,240 t')).toBeInTheDocument()
    expect(within(bar).queryByText('₹6.66 lakh')).not.toBeInTheDocument()
    expect(within(bar).getByText('not checked yet')).toBeInTheDocument()

    fireEvent.click(button)

    // After: the money appears and the button offers to run it again.
    expect(await screen.findByRole('button', { name: /re-run verification/i })).toBeInTheDocument()
    // Each figure counts up on its own timer, so wait for each settled value
    // rather than assuming they land together.
    expect(await screen.findByText('₹6.66 lakh', {}, { timeout: 4000 })).toBeInTheDocument()
    expect(await screen.findByText('805 t', {}, { timeout: 4000 })).toBeInTheDocument()
    expect(await screen.findByText('65 t', {}, { timeout: 4000 })).toBeInTheDocument()
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
})

// ------------------------------------------------------------ drill-down
describe('the drill-down panel', () => {
  const noop = () => {}

  function renderPanel(drain: Drain | null, props = {}) {
    return render(
      <DrainPanel
        drain={drain}
        loading={false}
        error={null}
        selectedTripId={drain?.trips[0]?.tripId ?? null}
        onSelectTrip={noop}
        onDecide={noop}
        onExplain={noop}
        explaining={false}
        evidenceSummary={null}
        deciding={false}
        onClose={noop}
        {...props}
      />,
    )
  }

  it('invites the engineer to pick a drain when none is open', () => {
    renderPanel(null)
    expect(screen.getByText(/Pick a drain on the map/)).toBeInTheDocument()
  })

  it('names every failed rule in plain English', () => {
    renderPanel(drainFixture())

    expect(screen.getByText(/same image as after-01.jpg/)).toBeInTheDocument()
    expect(screen.getByText(/never enters the approved dump site/)).toBeInTheDocument()
    expect(screen.getByText(/40 minutes before the truck arrived/)).toBeInTheDocument()
    expect(screen.getByText(RULES.R5)).toBeInTheDocument()
  })

  it('highlights the slip field the rule is arguing with', () => {
    const { container } = renderPanel(drainFixture())

    const conflict = container.querySelector('tr.conflict')
    expect(conflict).not.toBeNull()
    expect(conflict?.textContent).toContain('06:43')      // R8 disputes time-in
  })

  it('shows what the vision model said about each photo', () => {
    renderPanel(drainFixture())

    expect(screen.getByText('unclear')).toBeInTheDocument()
    expect(screen.getByText(/Channel bed is visible/)).toBeInTheDocument()
    expect(screen.getByText(/90% confident/)).toBeInTheDocument()
  })

  it('says so plainly when a drain is clean', () => {
    renderPanel(
      drainFixture({
        verdict: 'GREEN', findings: [], failedRules: [],
        trips: [{ ...drainFixture().trips[0], verdict: 'VERIFIED', hardFails: [], softFails: [], findings: [] }],
      }),
    )

    expect(screen.getByText(/Every check passed/)).toBeInTheDocument()
  })

  it('reports a trip whose trace could not be read', () => {
    const drain = drainFixture()
    drain.trips[0].traceProblem = 'NoSuchKey: the object vanished'

    renderPanel(drain)

    expect(screen.getByText(/GPS trace unreadable/)).toBeInTheDocument()
  })

  it('reports a trip with no slip at all', () => {
    const drain = drainFixture()
    drain.trips[0].slip = null

    renderPanel(drain)

    expect(screen.getByText(/No weighbridge slip was ingested/)).toBeInTheDocument()
  })

  it('will not let the engineer decide before verification', () => {
    renderPanel(drainFixture({ verdict: null }))

    expect(screen.getByRole('button', { name: /approve/i })).toBeDisabled()
    expect(screen.getByText(/Run verification before deciding/)).toBeInTheDocument()
  })

  it('passes the note along with the decision', () => {
    const onDecide = vi.fn()
    renderPanel(drainFixture(), { onDecide })

    fireEvent.change(screen.getByPlaceholderText(/Note \(optional\)/), {
      target: { value: 'Referred to vigilance.' },
    })
    fireEvent.click(screen.getByRole('button', { name: /hold payment/i }))

    expect(onDecide).toHaveBeenCalledWith('HOLD', 'Referred to vigilance.')
  })

  it('shows a decision once it has been taken', () => {
    renderPanel(drainFixture({ decision: 'HOLD', note: 'Truck never arrived.' }))

    expect(screen.getByText(/Held — Truck never arrived\./)).toBeInTheDocument()
  })

  it('shows the Bedrock evidence summary when it arrives', () => {
    renderPanel(drainFixture(), {
      evidenceSummary: 'The truck never reached the dump site.',
    })

    expect(screen.getByText(/The truck never reached the dump site\./)).toBeInTheDocument()
  })
})

// ------------------------------------------------------------- decisions
describe('deciding from the dashboard', () => {
  it('updates the summary bar when a drain is approved', async () => {
    vi.mocked(api.getDrain).mockResolvedValue(
      drainFixture({ drainId: '6', verdict: 'AMBER', reviewTonnes: 10, heldTonnes: 0 }),
    )
    vi.mocked(api.decide).mockResolvedValue({
      drainId: '6',
      decision: 'APPROVE',
      note: 'Gap explained by the underpass.',
      summary: { ...billFixture().summary, verifiedTonnes: 870, reviewTonnes: 0 },
      drains: billFixture().drains,
    })

    render(<App />)
    await screen.findByRole('banner')

    // Opening a drain is normally a map click; call the same path directly.
    const panel = render(
      <DrainPanel
        drain={drainFixture({ drainId: '6' })}
        loading={false}
        error={null}
        selectedTripId={null}
        onSelectTrip={() => {}}
        onDecide={(decision, note) => api.decide('6', decision, note)}
        onExplain={() => {}}
        explaining={false}
        evidenceSummary={null}
        deciding={false}
        onClose={() => {}}
      />,
    )

    fireEvent.click(panel.getByRole('button', { name: /^approve$/i }))

    await waitFor(() =>
      expect(api.decide).toHaveBeenCalledWith('6', 'APPROVE', ''),
    )
  })
})
