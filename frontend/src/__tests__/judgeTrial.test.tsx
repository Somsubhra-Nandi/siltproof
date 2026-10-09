// The judge-trial screens against a scripted API: create, collect, analyse,
// reset. The network seam is the feature's own api module.

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../features/judge-trial/TrialMap', () => ({ default: () => <div data-testid="trial-map" /> }))

vi.mock('../features/judge-trial/api', async () => {
  const actual = await vi.importActual<typeof import('../features/judge-trial/api')>('../features/judge-trial/api')
  return {
    ...actual,
    trialsAvailable: vi.fn(() => true),
    createTrial: vi.fn(),
    getTrial: vi.fn(),
    putDetails: vi.fn(),
    requestUpload: vi.fn(),
    uploadToStorage: vi.fn(),
    completeUpload: vi.fn(),
    retryEvidence: vi.fn(),
    deleteEvidence: vi.fn(),
    analyzeTrial: vi.fn(),
    getResults: vi.fn(),
    deleteTrial: vi.fn(),
  }
})

import * as api from '../features/judge-trial/api'
import JudgeTrial from '../features/judge-trial'
import type { Analysis, Evidence, Trial } from '../features/judge-trial/types'

const MB = 1024 * 1024
const TRIAL_ID = 'tr_abcdefghijklmnopqrstuvwxyz'

function makeTrial(evidence: Evidence[] = [], details = {}): Trial {
  return {
    trialId: TRIAL_ID,
    kind: 'judge',
    label: 'Judge 1',
    status: 'CREATED',
    createdAt: '2026-10-09T06:00:00+00:00',
    expiresAt: new Date(Date.now() + 48 * 3600_000).toISOString(),
    details,
    usage: { files: evidence.length, bytesReserved: 0, bedrockCalls: 0, textractCalls: 0, analyses: 0 },
    limits: {
      maxFilesPerTrial: 24,
      maxBytesPerTrial: 120 * MB,
      maxRetries: 2,
      maxAnalyses: 30,
      photoRoles: ['before', 'after', 'current', 'additional'],
      groups: {
        photo: { contentTypes: ['image/jpeg', 'image/png'], maxBytes: 15 * MB, maxFiles: 12 },
        slip: { contentTypes: ['application/pdf', 'image/jpeg', 'image/png'], maxBytes: 10 * MB, maxFiles: 6 },
        bill: { contentTypes: ['application/pdf', 'image/jpeg', 'image/png'], maxBytes: 10 * MB, maxFiles: 2 },
        trace: { contentTypes: ['application/geo+json', 'application/json'], maxBytes: 2 * MB, maxFiles: 4 },
      },
    },
    evidence,
    hasResults: false,
    lastAnalyzedAt: null,
    mockAws: false,
    privacy: { retentionHours: 48, services: [], notice: '' },
  }
}

const READY_PHOTO: Evidence = {
  evidenceId: 'ev_aaaaaaaaaaaaaaaaaaaa',
  group: 'photo',
  role: 'after',
  filename: 'IMG_1.jpg',
  contentType: 'image/jpeg',
  declaredSizeBytes: 7_700_000,
  sizeBytes: 7_700_000,
  sha256: 'x',
  state: 'READY',
  error: null,
  attempts: 1,
  createdAt: '',
  updatedAt: '',
  previewUrl: 'https://example.invalid/preview',
  result: {
    exif: { timestamp: '2026-10-09T09:53:12', timestampHasOffset: false, lat: 22.5801, lon: 88.4712, hasGps: true, camera: 'Phone', problems: [] },
    pHash: 'abcd',
    vision: {
      cleared: false, loadType: 'unclear', confidence: 0.4, notes: 'Water and vegetation fill the channel.', ok: true,
      modelId: 'apac.amazon.nova-pro-v1:0', mocked: false, ran: true, skippedReason: null, input: 'original',
    },
  },
}

const ANALYSIS: Analysis = {
  trialId: TRIAL_ID,
  analysisId: 'an_1',
  analyzedAt: '2026-10-09T06:10:00+00:00',
  analysisCount: 1,
  evidenceConsidered: [READY_PHOTO.evidenceId],
  evidenceExcluded: [],
  checks: [
    { id: 'R4', title: 'After-photo shows a cleared channel', subject: { evidenceId: READY_PHOTO.evidenceId, filename: 'IMG_1.jpg', group: 'photo', role: 'after' },
      status: 'REVIEW', severity: 'soft', message: 'IMG_1.jpg is labelled after, but the model does not see a cleared channel.', missing: [], basis: 'model', evidence: {} },
    { id: 'R5', title: 'GPS trace enters the designated disposal site', subject: null,
      status: 'NOT_EVALUATED', severity: null, message: 'Not evaluated: insufficient evidence.', missing: ['a truck GPS trace'], basis: null, evidence: {} },
  ],
  counts: { PASS: 0, CONSISTENT: 0, REVIEW: 1, FAIL: 0, INCONCLUSIVE: 0, NOT_EVALUATED: 1 },
  observations: [{ evidenceId: READY_PHOTO.evidenceId, filename: 'IMG_1.jpg', kind: 'vision', role: 'after',
    vision: READY_PHOTO.result!.vision, exif: READY_PHOTO.result!.exif, pHash: 'abcd', processingCopy: null }],
  summary: { text: '2 checks: 1 need review. Not evaluated is not passed.', generatedBy: 'template' },
  provenance: { mockAws: false, mocked: false, visionModelId: 'apac.amazon.nova-pro-v1:0', textract: 'AnalyzeDocument QUERIES', rulesVersion: 'trial-1', notes: [] },
}

const mocked = vi.mocked(api)

beforeEach(() => {
  sessionStorage.clear()
  vi.clearAllMocks()
  mocked.trialsAvailable.mockReturnValue(true)
})
afterEach(() => sessionStorage.clear())

async function startTrial(evidence: Evidence[] = []) {
  mocked.createTrial.mockResolvedValue({ trialId: TRIAL_ID, accessToken: 'secret-token', expiresAt: '', limits: makeTrial().limits, privacy: makeTrial().privacy })
  mocked.getTrial.mockResolvedValue(makeTrial(evidence))
  render(<JudgeTrial />)
  const start = screen.getByRole('button', { name: /start a trial/i })
  expect(start).toBeDisabled()
  fireEvent.click(screen.getByLabelText(/will not upload sensitive records/i))
  fireEvent.click(start)
  await screen.findByRole('heading', { name: 'Contractor bill' })
}

describe('judge trial', () => {
  it('explains itself and needs the API', () => {
    mocked.trialsAvailable.mockReturnValue(false)
    render(<JudgeTrial />)
    expect(screen.getByText(/Trials need the SiltProof API/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /start a trial/i })).toBeNull()
    expect(screen.getByText(/deleted about 48 hours/)).toBeInTheDocument()
  })

  it('creates a trial and keeps its token only for this tab', async () => {
    await startTrial()
    expect(mocked.createTrial).toHaveBeenCalledWith({ label: undefined, kind: 'judge', inviteCode: undefined })
    expect(JSON.parse(sessionStorage.getItem('siltproof.judgeTrial')!)).toEqual({ trialId: TRIAL_ID, token: 'secret-token' })
    for (const name of ['Contractor bill', 'Drain photographs', 'Weighbridge slips', 'Truck GPS trace', 'Drain location', 'Designated disposal site']) {
      expect(screen.getByRole('heading', { name })).toBeInTheDocument()
    }
  })

  it('shows a creation error from the server', async () => {
    mocked.createTrial.mockRejectedValue(new api.TrialApiError(403, 'INVITE_REQUIRED', 'A valid invite code is needed to start a trial.', {}))
    render(<JudgeTrial />)
    fireEvent.click(screen.getByLabelText(/will not upload sensitive records/i))
    fireEvent.click(screen.getByRole('button', { name: /start a trial/i }))
    expect(await screen.findByText(/valid invite code/)).toBeInTheDocument()
  })

  it('uploads a photo through register, storage and complete', async () => {
    await startTrial()
    mocked.requestUpload.mockResolvedValue({
      evidenceId: READY_PHOTO.evidenceId,
      upload: { method: 'POST', url: 'https://bucket', fields: { key: 'k' }, expiresInSeconds: 300, maxBytes: 10 },
      evidence: READY_PHOTO,
    })
    mocked.uploadToStorage.mockImplementation(async (_ticket, _file, progress) => progress(1))
    mocked.completeUpload.mockResolvedValue({ evidence: READY_PHOTO })
    mocked.getTrial.mockResolvedValue(makeTrial([READY_PHOTO]))

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'after' } })
    const input = document.querySelector('section[aria-labelledby="jt-group-2"] input[type=file]') as HTMLInputElement
    const file = new File([new Uint8Array(10)], 'IMG_1.jpg', { type: 'image/jpeg' })
    fireEvent.change(input, { target: { files: [file] } })

    await screen.findByText('IMG_1.jpg')
    expect(mocked.requestUpload).toHaveBeenCalledWith(
      { trialId: TRIAL_ID, token: 'secret-token' },
      { group: 'photo', role: 'after', filename: 'IMG_1.jpg', contentType: 'image/jpeg', sizeBytes: 10 },
    )
    expect(mocked.completeUpload).toHaveBeenCalled()
    expect(screen.getByText(/GPS 22.580100, 88.471200/)).toBeInTheDocument()
  })

  it('refuses an unsupported file without calling the API', async () => {
    await startTrial()
    const input = document.querySelector('section[aria-labelledby="jt-group-4"] input[type=file]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [new File(['lat,lon'], 'trace.csv', { type: 'text/csv' })] } })
    expect(await screen.findByText(/text\/csv is not accepted here/)).toBeInTheDocument()
    expect(mocked.requestUpload).not.toHaveBeenCalled()
  })

  it('shows server validation errors next to the field', async () => {
    await startTrial()
    mocked.putDetails.mockRejectedValue(new api.TrialApiError(400, 'INVALID_DETAILS', 'Some details are not valid.', {
      fields: { 'disposalSite.radiusM': 'must be at most 2000' },
    }))
    const section = document.querySelector('section[aria-labelledby="jt-group-6"]') as HTMLElement
    fireEvent.change(section.querySelector('input')!, { target: { value: '22.56, 88.43' } })
    fireEvent.change(section.querySelectorAll('input')[1], { target: { value: '5000' } })
    fireEvent.click(section.querySelector('button[type=submit]')!)
    expect(await screen.findByText('must be at most 2000')).toBeInTheDocument()
  })

  it('runs analysis and shows findings, not-evaluated checks and the AI observation apart', async () => {
    await startTrial([READY_PHOTO])
    mocked.analyzeTrial.mockResolvedValue(ANALYSIS)
    mocked.getTrial.mockResolvedValue(makeTrial([READY_PHOTO]))
    fireEvent.click(await screen.findByRole('button', { name: 'Run analysis' }))

    expect(await screen.findByRole('heading', { name: 'What the evidence supports' })).toBeInTheDocument()
    expect(screen.getByText('NOT EVALUATED — INSUFFICIENT EVIDENCE')).toBeInTheDocument()
    expect(screen.getByText(/Needs: a truck GPS trace/)).toBeInTheDocument()
    expect(screen.getByText(/AI observation, not a finding/)).toBeInTheDocument()
    expect(screen.getByText(/Water and vegetation fill the channel/)).toBeInTheDocument()
    expect(screen.getByText(/no model wrote it/)).toBeInTheDocument()
    expect(screen.getByAltText(/Uploaded photo IMG_1.jpg/)).toHaveAttribute('src', 'https://example.invalid/preview')
  })

  it('surfaces a 409 while files are still processing', async () => {
    await startTrial([READY_PHOTO])
    mocked.analyzeTrial.mockRejectedValue(new api.TrialApiError(409, 'EVIDENCE_PROCESSING', 'Some files are still being processed.', {}))
    fireEvent.click(await screen.findByRole('button', { name: 'Run analysis' }))
    expect(await screen.findByText(/still being processed/)).toBeInTheDocument()
  })

  it('starts another trial, deleting this one, with nothing carried over', async () => {
    await startTrial([READY_PHOTO])
    mocked.deleteTrial.mockResolvedValue({ deleted: TRIAL_ID })
    fireEvent.click(screen.getByRole('button', { name: 'Start another trial' }))
    fireEvent.click(screen.getByRole('button', { name: /Delete this trial now/ }))
    expect(await screen.findByRole('heading', { name: 'Bring your own evidence' })).toBeInTheDocument()
    expect(mocked.deleteTrial).toHaveBeenCalledWith({ trialId: TRIAL_ID, token: 'secret-token' })
    expect(sessionStorage.getItem('siltproof.judgeTrial')).toBeNull()
  })

  it('forgets an expired trial on reload', async () => {
    sessionStorage.setItem('siltproof.judgeTrial', JSON.stringify({ trialId: TRIAL_ID, token: 't' }))
    mocked.getTrial.mockRejectedValue(new api.TrialApiError(410, 'TRIAL_EXPIRED', 'expired', {}))
    render(<JudgeTrial />)
    expect(await screen.findByText('That trial has expired.')).toBeInTheDocument()
    await waitFor(() => expect(sessionStorage.getItem('siltproof.judgeTrial')).toBeNull())
  })
})
