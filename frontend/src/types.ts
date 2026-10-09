// Mirrors what backend/api/app.py returns.

export type Verdict = 'RED' | 'AMBER' | 'GREEN' | null
export type TripVerdict = 'HOLD' | 'REVIEW' | 'VERIFIED' | null
export type Decision = 'APPROVE' | 'HOLD' | null

export interface Summary {
  ratePerTonne: number
  claimedTonnes: number
  verifiedTonnes: number
  reviewTonnes: number
  heldTonnes: number
  claimedRupees: number
  verifiedRupees: number
  reviewRupees: number
  heldRupees: number
  drainCount: number
  red: number
  amber: number
  green: number
  decided: number
  pendingTonnes?: number
}

export interface DrainRow {
  drainId: string
  name: string
  claimedTonnes: number
  verifiedTonnes: number
  reviewTonnes: number
  heldTonnes: number
  verdict: Verdict
  decision: Decision
  note: string | null
  tripCount: number
  failedRules: string[]
}

export interface MissingEvidence {
  tripsWithoutSlip: number
  tripsWithoutTrace: number
  unreadableTraces: number
  evidenceErrors: number
  drainsWithoutPhotos: number
}

export interface Bill {
  billId: string
  contractor: string
  ward: string
  status: string
  verifiedAt: string | null
  verificationMs?: number
  workWindow: [string, string]
  simulated: boolean
  summary: Summary
  drains: DrainRow[]
  missingEvidence?: MissingEvidence | null
  rules: Record<string, string>
}

export interface Finding {
  rule: string
  severity: 'hard' | 'soft'
  rule_text: string
  message: string
  evidence: Record<string, unknown>
}

export interface SlipField {
  value: string | number | null
  raw: string | null
  confidence: number
  ok: boolean
  lowConfidence?: boolean
}

export interface Slip {
  s3Key: string
  ticketNo: string | null
  vehicleNo: string | null
  gross: number | null
  tare: number | null
  net: number | null
  timeIn: string | null
  timeOut: string | null
  site: string | null
  fields: Record<string, SlipField> | null
  confidenceAvg: number
  missingFields: string[]
  lowConfidenceFields: string[]
}

export interface Photo {
  s3Key: string
  /** A five-minute presigned link live, a local path offline, null if none. */
  imageUrl?: string | null
  role: string | null
  status: string
  lat: number | null
  lon: number | null
  timestamp: string | null
  hasGps: boolean
  pHash: string | null
  problems: string[]
  bedrock: {
    cleared: boolean | null
    loadType: string | null
    confidence: number | null
    notes: string | null
    modelId: string | null
    /** True when offline fixture text stands in for a model call. */
    mocked?: boolean
    ok: boolean | null
  }
}

export interface Trip {
  tripId: string
  tripNo: string
  vehicleNo: string
  claimedTonnes: number
  verdict: TripVerdict
  hardFails: string[]
  softFails: string[]
  findings: Finding[]
  startTime: string | null
  arrivalTime: string | null
  slip: Slip | null
  /** Sits on the trip, not the slip: null whenever there is no slip. */
  slipImageUrl?: string | null
  actualRoute: [number, number][]
  actualRouteDistanceM: number
  tracePointCount: number
  traceProblem: string | null
}

export interface Drain {
  billId: string
  drainId: string
  name: string
  verdict: Verdict
  decision: Decision
  note: string | null
  decidedAt: string | null
  claimedTonnes: number
  verifiedTonnes: number
  reviewTonnes: number
  heldTonnes: number
  lengthM: number
  widthM: number
  depthM: number
  plausibleMaxTonnes: number
  claimedRoute: [number, number][] | null
  dumpsite: { name: string; center: [number, number]; geofence: unknown }
  findings: Finding[]
  failedRules: string[]
  summary: string | null
  /** Null offline, where the summary is assembled from the findings. */
  summaryModelId?: string | null
  /** Seconds the evidence links stay valid; null offline (local paths). */
  evidenceUrlExpiresInSeconds?: number | null
  photos: Photo[]
  trips: Trip[]
  rules: Record<string, string>
}

export interface DecisionResult {
  drainId: string
  decision: Decision
  note: string | null
  summary: Summary
  drains: DrainRow[]
}
