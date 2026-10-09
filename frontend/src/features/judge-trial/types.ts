// Shapes from docs/JUDGE-TRIAL-API.md. Only what the screens read is typed.

export type LonLat = [number, number]

export type Group = 'bill' | 'photo' | 'slip' | 'trace'
export type PhotoRole = 'before' | 'after' | 'current' | 'additional'

export type EvidenceState =
  | 'UPLOADING'
  | 'UPLOADED'
  | 'QUEUED'
  | 'PROCESSING'
  | 'READY'
  | 'FAILED'
  | 'REJECTED'

export type DrainSource = 'manual_point' | 'map_selected' | 'field_approximate' | 'user_geometry'

export type DrainLocation = {
  point: LonLat
  line?: LonLat[]
  toleranceM: number
  source: DrainSource
  derivedFromPhotos: boolean
  name?: string | null
  lengthM?: number
  widthM?: number
  depthM?: number
}

export type DisposalSite = {
  point: LonLat
  radiusM: number
  polygon?: LonLat[]
  name?: string | null
}

export type Claim = {
  quantityTonnes?: number
  ratePerTonne?: number
  amountRupees?: number
  contractor?: string | null
  reference?: string | null
}

export type WorkWindow = { start: string; end: string }
export type Truck = { vehicleNo?: string | null; capacityTonnes?: number }

export type Details = {
  drainLocation?: DrainLocation
  disposalSite?: DisposalSite
  claim?: Claim
  workWindow?: WorkWindow
  truck?: Truck
}

export type SlipField = {
  value: string | number | null
  raw?: string | null
  confidence: number
  ok: boolean
  lowConfidence?: boolean
  derived?: string
}

export type Vision = {
  cleared: boolean | null
  loadType: string | null
  confidence: number | null
  notes: string
  ok: boolean
  problems?: string[]
  modelId: string | null
  mocked: boolean
  ran: boolean
  skippedReason: string | null
  input: 'original' | 'processing_copy'
}

export type Exif = {
  timestamp: string | null
  timestampHasOffset: boolean
  lat: number | null
  lon: number | null
  hasGps: boolean
  altitudeM?: number | null
  gpsAccuracyM?: number | null
  camera?: string | null
  width?: number
  height?: number
  orientation?: number | null
  problems: string[]
}

export type ProcessingCopy = {
  key: string
  originalKey: string
  originalSha256: string
  copySha256: string
  originalPixels: [number, number]
  copyPixels: [number, number]
}

export type EvidenceResult = {
  // photo
  exif?: Exif
  pHash?: string | null
  vision?: Vision
  processingCopy?: ProcessingCopy | null
  // slip
  fields?: Record<string, SlipField>
  confidenceAvg?: number
  missingFields?: string[]
  lowConfidenceFields?: string[]
  mocked?: boolean
  // trace
  vehicleNo?: string | null
  pointCount?: number
  startTime?: string
  endTime?: string
  distanceM?: number
  maxGapSeconds?: number | null
  route?: LonLat[]
  warnings?: string[]
  // bill
  extraction?: string
  note?: string
}

export type Evidence = {
  evidenceId: string
  group: Group
  role: PhotoRole | null
  filename: string
  contentType: string
  declaredSizeBytes: number
  sizeBytes: number | null
  sha256: string | null
  state: EvidenceState
  error: { code: string; message: string } | null
  attempts: number
  createdAt: string
  updatedAt: string
  result: EvidenceResult | null
  previewUrl?: string | null
  retryable?: boolean
}

export type GroupLimits = { contentTypes: string[]; maxBytes: number; maxFiles: number }

export type Limits = {
  maxFilesPerTrial: number
  maxBytesPerTrial: number
  maxRetries: number
  maxAnalyses: number
  groups: Record<Group, GroupLimits>
  photoRoles: PhotoRole[]
}

export type Privacy = { retentionHours: number; services: string[]; notice: string }

export type Trial = {
  trialId: string
  kind: 'judge' | 'field'
  label: string | null
  status: string
  createdAt: string
  expiresAt: string
  details: Details
  usage: { files: number; bytesReserved: number; bedrockCalls: number; textractCalls: number; analyses: number }
  limits: Limits
  evidence: Evidence[]
  hasResults: boolean
  lastAnalyzedAt: string | null
  mockAws: boolean
  privacy: Privacy
}

export type CheckStatus = 'PASS' | 'CONSISTENT' | 'REVIEW' | 'FAIL' | 'INCONCLUSIVE' | 'NOT_EVALUATED'

export type Check = {
  id: string
  title: string
  subject: { evidenceId: string; filename: string | null; group: Group; role: PhotoRole | null } | null
  status: CheckStatus
  severity: 'hard' | 'soft' | null
  message: string
  missing: string[]
  basis: 'approximate' | 'supplied' | 'extracted' | 'model' | 'mocked' | null
  evidence: Record<string, unknown>
  mockOutcome?: CheckStatus
}

export type Observation = {
  evidenceId: string
  filename: string | null
  kind: 'vision' | 'textract' | 'trace' | 'bill'
  role?: PhotoRole | null
  vision?: Vision | null
  exif?: Exif | null
  pHash?: string | null
  processingCopy?: ProcessingCopy | null
  fields?: Record<string, SlipField> | null
  confidenceAvg?: number
  missingFields?: string[]
  lowConfidenceFields?: string[]
  mocked?: boolean
  route?: LonLat[]
  warnings?: string[]
  pointCount?: number
  distanceM?: number
  maxGapSeconds?: number | null
  vehicleNo?: string | null
  startTime?: string
  endTime?: string
  extraction?: string
  note?: string
}

export type Analysis = {
  trialId: string
  analysisId: string
  analyzedAt: string
  analysisCount: number
  evidenceConsidered: string[]
  evidenceExcluded: Array<{ evidenceId: string; filename: string | null; state: string; reason: string }>
  checks: Check[]
  counts: Record<CheckStatus, number>
  observations: Observation[]
  summary: { text: string; generatedBy: string }
  provenance: {
    mockAws: boolean
    mocked: boolean
    visionModelId: string | null
    textract: string
    rulesVersion: string
    notes: string[]
  }
}

export type CreatedTrial = {
  trialId: string
  accessToken: string
  expiresAt: string
  limits: Limits
  privacy: Privacy
}

export type UploadTicket = {
  evidenceId: string
  upload: {
    method: 'POST'
    url: string
    fields: Record<string, string>
    expiresInSeconds: number
    maxBytes: number
  }
  evidence: Evidence
}

export type Session = { trialId: string; token: string }
