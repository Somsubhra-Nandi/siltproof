# Judge trial API — contract

Status: **v1, implemented on `feat/judge-self-service`**, offline-tested only
(moto + `MOCK_AWS=1`, and a headless-browser run against `scripts/trial_dev_server.py`). Nothing here has been deployed or called against live
AWS.

This is the contract between the trial backend and any frontend that uses it:
the "Try SiltProof Yourself" screens, and the real Kolkata field case
(`docs/KOLKATA-FIELD-EVIDENCE.md`), which is just another trial. There is one
trial API, not one per use.

Bill B1 and its routes (`/verify`, `/bill`, `/drain`, `/decision`,
`/upload-url`) are not changed by any of this.

---

## 1. Identifiers

| What | Format | Source |
|---|---|---|
| Trial ID | `tr_` + 26 chars of `[a-z2-7]` (128 random bits, base32) | server, `secrets` |
| Evidence ID | `ev_` + 20 chars of `[a-z2-7]` (100 random bits) | server, `secrets` |
| Access token | 43 chars, URL-safe base64 (256 random bits) | server, `secrets`, returned **once** |

The server stores only the SHA-256 of the access token. IDs are validated with
`^tr_[a-z2-7]{26}$` and `^ev_[a-z2-7]{20}$` before any lookup; anything else
is a 404.

Clients never supply an S3 key, an evidence ID for a new file, or a trial ID
for a new trial.

## 2. Access control

Every route under `/trials/{trialId}` requires

```
Authorization: Bearer <accessToken>
```

The server hashes the presented token and compares it with the stored hash in
constant time. A missing, wrong, or other trial's token gets **404
`TRIAL_NOT_FOUND`**, the same answer as a trial that does not exist, so a
token cannot be used to probe for other trial IDs.

What this is and is not:

- It is a **capability**: whoever holds the token can read and change that
  one trial, nothing else. It is not a user login, and the trial ID alone is
  never enough.
- The token lives in the browser tab's memory and `sessionStorage` (so a
  reload keeps the trial). Closing the tab forgets it. Lose the token and the
  trial is unreachable; it expires on its own (section 15).
- Trial creation can be gated by an **invite code** (`TRIAL_INVITE_CODE`,
  stack parameter `TrialInviteCode`, NoEcho). When it is set, `POST /trials`
  without the matching `inviteCode` is refused with 403. The code is typed by
  the judge; it must never be baked into the frontend bundle.
- No trial route can read or write Bill B1 or any `BILL#`, `EVID#`,
  `DUMPSITE#` or `VEHICLE#` item, and no trial S3 key falls outside
  `trials/{trialId}/`.

## 3. Trial creation

`POST /trials`

```json
{ "label": "Judge trial", "kind": "judge", "inviteCode": "..." }
```

| Field | Rules |
|---|---|
| `label` | optional, ≤ 80 chars, control characters stripped |
| `kind` | `judge` (default) or `field`. Display only; same data model and API |
| `inviteCode` | required only when the deployment sets one |

**201**

```json
{
  "trialId": "tr_…",
  "accessToken": "…",          // shown once; send as Bearer
  "createdAt": "2026-10-09T06:00:00+00:00",
  "expiresAt": "2026-10-11T06:00:00+00:00",
  "limits": { … section 9 … },
  "privacy": { "retentionHours": 48, "services": ["Amazon S3", "Amazon DynamoDB",
               "Amazon Textract", "Amazon Bedrock (Amazon Nova Pro)"], "notice": "…" }
}
```

Creation reserves one unit of the global daily trial quota first (section 16);
when that is exhausted the answer is 429 `TRIAL_QUOTA_EXHAUSTED`.

## 4. Endpoints

All bodies are JSON, all responses are JSON with `Cache-Control: no-store`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/trials` | create a trial (no token; invite code if configured) |
| GET | `/trials/{trialId}` | trial, details, evidence list with states and preview links |
| PUT | `/trials/{trialId}/details` | set or clear drain location, disposal site, claim, work window, truck |
| POST | `/trials/{trialId}/upload-url` | register one file and get a presigned S3 POST |
| POST | `/trials/{trialId}/evidence/{evidenceId}/complete` | tell the server the upload finished; it validates and starts processing |
| POST | `/trials/{trialId}/evidence/{evidenceId}/retry` | re-queue a FAILED item (bounded) |
| DELETE | `/trials/{trialId}/evidence/{evidenceId}` | remove one file and its record |
| POST | `/trials/{trialId}/analyze` | run the checks over whatever evidence is READY |
| GET | `/trials/{trialId}/results` | the latest analysis |
| DELETE | `/trials/{trialId}` | delete the trial and every object now |

Path names follow the brief. The only addition is `…/complete`, because an S3
upload acknowledgement is not proof that a file is valid or processed
(section 7).

## 5. Request schemas

### `PUT /trials/{trialId}/details`

Any subset of the keys below. A key set to `null` clears it. Omitted keys are
left alone. Every field is validated server-side; one bad field rejects the
whole request with 400 `INVALID_DETAILS` and a `fields` map.

```jsonc
{
  "drainLocation": {
    "point": [88.4712, 22.5801],          // [lon, lat], required unless `line`
    "line": [[lon, lat], …],              // optional centreline, 2-200 points
    "toleranceM": 30,                      // 5-500, default 30
    "source": "manual_point",             // manual_point | map_selected |
                                           // field_approximate | user_geometry
    "derivedFromPhotos": false,           // true makes R1 circular → INCONCLUSIVE
    "name": "Channel behind block C",     // optional, ≤ 80
    "lengthM": 120, "widthM": 2.5, "depthM": 0.8   // optional, for R10
  },
  "disposalSite": {
    "point": [88.40, 22.55],              // [lon, lat]
    "radiusM": 150,                        // 20-2000, used when no polygon
    "polygon": [[lon, lat], …],           // optional ring, 4-200 points
    "name": "Designated site for this trial"
  },
  "claim": {
    "quantityTonnes": 50,                 // > 0, ≤ 100000
    "ratePerTonne": 1800,                 // > 0, ≤ 1000000 (₹)
    "amountRupees": 90000,                // ≥ 0, ≤ 1e10
    "contractor": "Example Contractor",   // optional, ≤ 120
    "reference": "WO-123/2026"            // optional, ≤ 80
  },
  "workWindow": { "start": "2026-10-01T00:00:00+05:30",
                  "end":   "2026-10-09T23:59:59+05:30" },
  "truck": { "vehicleNo": "WB 25 AB 1234", "capacityTonnes": 10 }
}
```

`source` can never be `official` or `surveyed`: nothing a trial supplies is an
official drain map, and the API will not accept a claim that it is. The
disposal site is "designated for this trial"; the API never calls it approved
or authorized.

### `POST /trials/{trialId}/upload-url`

```json
{ "group": "photo", "role": "current", "filename": "IMG_0412.jpg",
  "contentType": "image/jpeg", "sizeBytes": 7712345 }
```

| Field | Rules |
|---|---|
| `group` | `bill`, `photo`, `slip`, `trace` |
| `role` | photos only: `before`, `after`, `current`, `additional` (default `current`). Never inferred |
| `filename` | display only, ≤ 120 chars after sanitising; never used in the S3 key |
| `contentType` | must be allowed for the group (section 9) |
| `sizeBytes` | 1 … the group's maximum; counted against the trial's byte budget |

## 6. Response schemas

### Evidence item (inside `GET /trials/{id}` and most evidence routes)

```jsonc
{
  "evidenceId": "ev_…",
  "group": "photo", "role": "current",
  "filename": "IMG_0412.jpg",
  "contentType": "image/jpeg",
  "declaredSizeBytes": 7712345,
  "sizeBytes": 7712345,              // measured from S3 after upload
  "sha256": "…",                     // of the original bytes, set by processing
  "state": "READY",                  // section 10
  "error": null,                     // { code, message } when FAILED / REJECTED
  "attempts": 1,
  "createdAt": "…", "updatedAt": "…",
  "previewUrl": "https://…",         // 300 s presigned GET of the ORIGINAL; images and PDFs only
  "previewExpiresInSeconds": 300,
  "result": { … per group, below … }
}
```

`result` for a **photo**:

```jsonc
{
  "exif": { "timestamp": "2026-10-09T09:53:12", "timestampHasOffset": false,
            "lat": 22.58, "lon": 88.47, "hasGps": true, "altitudeM": 0,
            "gpsAccuracyM": null, "camera": "…", "width": 4624, "height": 2080,
            "orientation": 0, "problems": [] },
  "pHash": "c3a1…",
  "vision": {                         // verbatim parsed model output, never edited
    "cleared": false, "loadType": "unclear", "confidence": 0.4,
    "notes": "…", "ok": true, "problems": [],
    "modelId": "apac.amazon.nova-pro-v1:0",   // null when mocked or not run
    "mocked": false, "ran": true,
    "skippedReason": null,           // e.g. "QUOTA_EXHAUSTED"
    "input": "original" | "processing_copy"
  },
  "processingCopy": null | { "key": "trials/…/processing/ev_….jpg",
     "originalKey": "…", "originalSha256": "…", "copySha256": "…",
     "originalPixels": [4624, 2080], "copyPixels": [1568, 705] }
}
```

`result` for a **slip**: the Textract parse as stored for B1 — `fields`
(`ticketNo`, `vehicleNo`, `gross`, `tare`, `net`, `timeIn`, `timeOut`, `site`,
each `{value, raw, confidence, ok, lowConfidence}`), `confidenceAvg`,
`missingFields`, `lowConfidenceFields`, `modelVersion`, `mocked`.

`result` for a **trace**: `vehicleNo`, `pointCount`, `startTime`, `endTime`,
`maxGapSeconds`, `distanceM`, `bbox`, `route` (downsampled `[lon, lat]`, ≤ 300
points), `warnings`.

`result` for a **bill**: `{ "stored": true, "extraction": "not_supported" }`.
SiltProof has no general bill extraction; the claim comes from the manual
`claim` fields, and the document is kept as supporting evidence.

### Analysis (`POST …/analyze`, `GET …/results`)

```jsonc
{
  "trialId": "tr_…",
  "analysisId": "an_…", "analyzedAt": "…", "analysisCount": 3,
  "evidenceConsidered": ["ev_…"],
  "evidenceExcluded": [{ "evidenceId": "ev_…", "state": "FAILED", "reason": "…" }],
  "checks": [
    {
      "id": "R1", "title": "Photo GPS against the drain location",
      "subject": { "evidenceId": "ev_…", "filename": "IMG_0412.jpg" },
      "status": "CONSISTENT",       // section 11
      "severity": null,             // "hard" | "soft" for FAIL / REVIEW
      "message": "…",
      "missing": [],                // prerequisites that were absent
      "basis": "approximate",       // "approximate" | "supplied" | "extracted" | "mocked"
      "evidence": { "distanceM": 18.2, "toleranceM": 30 }
    }
  ],
  "counts": { "PASS": 2, "CONSISTENT": 1, "REVIEW": 1, "FAIL": 0,
              "INCONCLUSIVE": 1, "NOT_EVALUATED": 6 },
  "observations": [ { "evidenceId": "ev_…", "kind": "vision", … } ],
  "summary": { "text": "…", "generatedBy": "template" },
  "provenance": {
     "mockAws": false,
     "visionModelId": "apac.amazon.nova-pro-v1:0",
     "textract": "AnalyzeDocument QUERIES",
     "rulesVersion": "trial-1",
     "notes": ["Rules run in the API Lambda; no model is called by /analyze."]
  }
}
```

`/analyze` never calls Bedrock or Textract. All billable work happens once per
file, at processing. The summary is a fixed template over the checks, labelled
as such, so it cannot contradict them.

## 7. Upload flow

```
POST /trials                         → trialId + token
PUT  /trials/{id}/details            → drain, disposal site, claim …   (any time)
POST /trials/{id}/upload-url         → evidence UPLOADING, presigned POST
     browser POSTs the file straight to S3 (multipart form, fields as given)
POST /trials/{id}/evidence/{e}/complete
     server: HEAD → size within limit and equal to the declared size
             ranged GET → file signature matches the declared type
             ok  → UPLOADED → QUEUED, processor invoked asynchronously
             bad → REJECTED, object deleted
GET  /trials/{id}                    → poll every 2-3 s until READY / FAILED
POST /trials/{id}/analyze            → checks over READY evidence
GET  /trials/{id}/results
```

Why a presigned **POST** and not a PUT: a POST policy carries a
`content-length-range` condition and an exact `Content-Type`, so S3 itself
refuses an oversized or retyped file. A presigned PUT cannot bound the size.
The policy is valid for **300 s** and fixes the key; the browser cannot choose
or change it. Large photos never pass through API Gateway (its payload limit
is far below 10 MB) or the Lambda request body.

The S3 key is `trials/{trialId}/originals/{evidenceId}{.ext}`, where `.ext`
comes from the validated content type. Processing copies go to
`trials/{trialId}/processing/{evidenceId}.jpg`. Nothing else is ever written
under `trials/`.

## 8. Supported evidence

| Group | Evidence group in the UI | Input |
|---|---|---|
| `bill` | 1 Contractor bill | document (PDF/JPEG/PNG) + manual `claim` fields |
| `photo` | 2 Drain photographs | JPEG (EXIF kept) or PNG (usually no EXIF GPS) |
| `slip` | 3 Weighbridge slips | JPEG, PNG, or a **single-page** PDF |
| `trace` | 4 Truck GPS trace | JSON (SiltProof trace schema) or GeoJSON LineString |
| — | 5 Drain location | `details.drainLocation` |
| — | 6 Disposal site | `details.disposalSite` |

### Trace formats (the only two accepted)

SiltProof trace JSON, as the B1 ingest already reads:

```json
{ "vehicleNo": "WB 25 AB 1234",
  "points": [ { "lat": 22.58, "lon": 88.47, "t": "2026-10-09T07:12:00+05:30" }, … ] }
```

GeoJSON Feature (or a FeatureCollection holding exactly one) with a LineString
and a parallel `properties.times` (or `coordTimes`) array:

```json
{ "type": "Feature", "properties": { "vehicleNo": "…", "times": ["…", "…"] },
  "geometry": { "type": "LineString", "coordinates": [[88.47, 22.58], [88.46, 22.57]] } }
```

Validation rejects the file (state `REJECTED`, code `INVALID_TRACE`) when:
fewer than 2 or more than 20,000 points; any coordinate missing, non-numeric,
or out of range; `[0, 0]`; any timestamp missing or not ISO 8601; timestamps
going backwards; a GeoJSON `times` array of the wrong length. Warnings (kept,
not rejected): mixed timezone/naive stamps, duplicate consecutive points, a
jump implying > 150 km/h, and a whole trace that would make sense with
latitude and longitude swapped. GPX and CSV are **not** supported.

## 9. File types and sizes

| Group | Content types | Max per file | Max files |
|---|---|---|---|
| `photo` | `image/jpeg`, `image/png` | 15 MB | 12 |
| `slip` | `image/jpeg`, `image/png`, `application/pdf` (1 page) | 10 MB (Textract sync limit) | 6 |
| `bill` | `application/pdf`, `image/jpeg`, `image/png` | 10 MB | 2 |
| `trace` | `application/json`, `application/geo+json` | 2 MB | 4 |

Per trial: at most **24 files** and **120 MB** in total (declared sizes are
reserved when the upload URL is issued and released if the file is rejected or
deleted).

Signatures checked at `…/complete`: JPEG `FF D8 FF`, PNG `89 50 4E 47 0D 0A
1A 0A`, PDF `%PDF-`, JSON first non-space byte `{` or `[` (and the whole file
must parse). A PDF slip with more than one page fails processing with
`PDF_TOO_MANY_PAGES` before Textract is called.

Photos larger than 8 MB, or with a side over 8,000 px, are sent to Bedrock as a
**processing copy** (longest side 1,568 px, JPEG q85, EXIF orientation applied).
The copy records the original's key, both SHA-256s and both pixel sizes. EXIF,
GPS, timestamp and pHash always come from the original bytes, before any
transformation. The original is never modified.

## 10. Evidence states

| State | Meaning |
|---|---|
| `UPLOADING` | upload URL issued; the server has not seen a file yet |
| `UPLOADED` | object present, size and signature verified |
| `QUEUED` | processor invoked; waiting to start |
| `PROCESSING` | EXIF / pHash / Bedrock / Textract running (lease of 120 s) |
| `READY` | processing finished; `result` is set (it may still record problems, e.g. no GPS) |
| `FAILED` | processing failed; `error` says why; may be retried |
| `REJECTED` | validation failed; the object was deleted; register the file again |

Bill documents and traces go straight from `UPLOADED` to `READY` or
`REJECTED` inside `…/complete` (no AI). An S3 upload alone never moves a
state. `READY` means "processed", not "passed".

## 11. Analysis states and results

`POST …/analyze` answers **409 `EVIDENCE_PROCESSING`** (with the pending
evidence IDs) while any file is `UPLOADING`-but-completed, `UPLOADED`,
`QUEUED` or `PROCESSING`; the UI polls and tries again. Items still
`UPLOADING` that were never completed are listed as excluded, not waited on.
`FAILED` and `REJECTED` items are listed under `evidenceExcluded`.

Check statuses:

| Status | Meaning |
|---|---|
| `PASS` | the check ran on adequate evidence and found nothing against the claim |
| `CONSISTENT` | ran against an **approximate** reference (a supplied point, a field-derived line); no contradiction, **not proof** |
| `REVIEW` | soft failure, for a person to look at |
| `FAIL` | hard failure under the B1 rule's own threshold |
| `INCONCLUSIVE` | evidence exists but cannot decide (unreadable field, `unclear` vision result, circular geometry, unknown accuracy) |
| `NOT_EVALUATED` | a prerequisite is missing; UI label **NOT EVALUATED — INSUFFICIENT EVIDENCE**; `missing` lists what |

Checks and prerequisites:

| ID | Check | Needs | Reuses |
|---|---|---|---|
| R1 | photo GPS vs drain location | photo GPS, `drainLocation` | `geo.distance_to_line_m`; B1's 30 m soft band, with the trial's `toleranceM` in place of the 30 m buffer |
| R2 | photo time inside the work window | photo timestamp, `workWindow` | `rules.parse_time`, `rules._aware` |
| R3 | reused photo within this trial | ≥ 2 photos with a pHash | `rules.find_duplicates` (threshold 12) |
| R4 | after-photo looks cleared | `role = after`, a non-mocked vision result | B1's R4 logic; `unclear` or `ok:false` → INCONCLUSIVE |
| R5 | trace enters the designated site | trace, `disposalSite` | `rules.trace_arrival` |
| R6 | consecutive traces physically possible | ≥ 2 traces of one vehicle | `rules.find_impossible_pairs` |
| R7 | slip net ≤ truck capacity | slip net, `truck.capacityTonnes` | `rules.check_trip` |
| R8 | slip time-in vs GPS arrival | slip time-in, a trace paired with it, `disposalSite` | `rules.check_trip` / `r8_message` |
| R9 | slip vehicle = trip vehicle | slip vehicle, trace or `truck` vehicle | `rules.check_trip` |
| R10 | claim fits the drain's volume | `claim.quantityTonnes`, drain length/width/depth | `rules.check_drain_volume` |
| GPS_GAP | trace dark > 3 min | trace | `rules.max_gap_seconds` |
| T1 | claim arithmetic: quantity × rate = amount (±₹1 or 0.5%) | all three claim numbers | trial-only |
| T2 | slip arithmetic: gross − tare = net (±0.05 t) | all three slip weights | trial-only |
| T3 | slips' net total vs claimed quantity | claim quantity, ≥ 1 slip net | trial-only |

Slip-to-trace pairing: one slip and one trace pair directly; otherwise a slip
pairs with the trace whose vehicle number matches; an unpaired slip leaves R8
`NOT_EVALUATED`. R1-R10 in `backend/common/rules.py` are imported, never
edited. The trial adaptor is `backend/trial/analysis.py`.

## 12. Missing evidence

A trial with only one photo still gets photo processing, EXIF, pHash and the
vision result; every other check comes back `NOT_EVALUATED` with its missing
prerequisites. No check is ever reported as `PASS` because its evidence was
absent, and the summary says how many checks could not be evaluated.

## 13. Errors

```json
{ "error": "Human-readable sentence.", "code": "MACHINE_CODE", "…": "context" }
```

| HTTP | Code | When |
|---|---|---|
| 400 | `INVALID_JSON`, `INVALID_REQUEST`, `INVALID_DETAILS`, `UNSUPPORTED_GROUP`, `UNSUPPORTED_TYPE`, `INVALID_ROLE`, `FILE_TOO_LARGE`, `EMPTY_FILE` | bad input |
| 403 | `INVITE_REQUIRED` | creation without the right invite code |
| 404 | `TRIAL_NOT_FOUND`, `EVIDENCE_NOT_FOUND`, `NO_ANALYSIS` | unknown, or the token does not match |
| 409 | `NOT_UPLOADED`, `BAD_STATE`, `EVIDENCE_PROCESSING`, `RETRY_LIMIT` | wrong order |
| 410 | `TRIAL_EXPIRED` | past `expiresAt` (data may not be deleted yet; it is not served) |
| 413 | `TRIAL_FILE_LIMIT`, `TRIAL_BYTES_LIMIT` | per-trial file or byte budget |
| 422 | `SIZE_MISMATCH`, `SIGNATURE_MISMATCH`, `INVALID_TRACE` | the uploaded object failed validation (also recorded on the item) |
| 429 | `TRIAL_QUOTA_EXHAUSTED`, `ANALYSIS_LIMIT` | daily trial creation, per-trial analyses |
| 503 | `PROCESSOR_UNAVAILABLE`, `STORAGE_UNAVAILABLE` | not configured |

Processing errors are stored on the item, not returned as HTTP errors:
`QUOTA_EXHAUSTED` (daily or per-trial model allowance; fail closed, no call
made), `BEDROCK_FAILED`, `TEXTRACT_FAILED`, `PDF_TOO_MANY_PAGES`,
`UNREADABLE_IMAGE`, `PROCESSING_TIMEOUT`.

## 14. Isolation

- DynamoDB: one partition per trial, `pk = TRIAL#{trialId}`; `sk = META`,
  `EVID#{evidenceId}`, `RESULT#LATEST`. Daily counters live under
  `pk = QUOTA#{yyyy-mm-dd}`. No trial code reads or writes `BILL#…`,
  `EVID#{s3 key}`, `DUMPSITE#…` or `VEHICLE#…`.
- S3: `trials/{trialId}/…` only. The existing ingest S3 notifications filter
  on `photos/`, `slips/` and `traces/`, so a trial upload never triggers B1
  ingestion; `config.kind_for_key` also classifies `trials/` as `UNKNOWN`.
- R3 duplicate search runs within one trial only. Trials never compare
  against B1 or other trials.
- Preview links are minted only for keys matching
  `^trials/{thisTrialId}/originals/{evidenceId}\.(jpg|png|pdf)$` taken from
  the trial's own item.

## 15. Cleanup and retention

- **Target: about 48 hours.** `expiresAt = createdAt + 48 h`. After that the
  API refuses the trial (410) even if data still exists.
- DynamoDB items carry `ttl` (epoch seconds, `expiresAt`). DynamoDB TTL
  deletes typically within a few days of expiry; it is not exact.
- S3 lifecycle rule on prefix `trials/`: expire after **2 days**. S3 counts
  days to the next midnight UTC and deletes asynchronously, so objects go
  between ~48 and ~72+ hours after upload. We do not promise exact timing.
- `DELETE /trials/{id}` deletes every object and item immediately (tested).
- Quota counters carry a `ttl` of 3 days.

## 16. Quotas and abuse prevention

Hard limits are enforced by DynamoDB **conditional atomic updates**
(`ADD count :1` with `count < :limit`), reserved **before** the call they
protect. A reservation is never refunded, even if the call then fails, because
a failed call may still have been billed. When a counter is full the server
fails closed.

| Counter | Default | Env var |
|---|---|---|
| Trials created per UTC day (global) | 60 | `TRIAL_DAILY_TRIALS` |
| Bedrock photo calls per UTC day (all trials) | 200 | `TRIAL_DAILY_BEDROCK_CALLS` |
| Textract calls per UTC day (all trials) | 100 | `TRIAL_DAILY_TEXTRACT_CALLS` |
| Bedrock calls per trial | 15 | `TRIAL_MAX_BEDROCK_PER_TRIAL` |
| Textract calls per trial | 8 | `TRIAL_MAX_TEXTRACT_PER_TRIAL` |
| Analyses per trial | 30 | `TRIAL_MAX_ANALYSES` |
| Retries per evidence item | 2 | — |

Also at deployment: API Gateway per-route throttling, which the template sets
on the trial routes only (`POST /trials` 1 rps / burst 5; upload-url,
complete and `GET /trials/{id}` 10 rps / burst 20; retry and analyze 2 rps /
burst 5), leaving the B1 routes on account defaults; and AWS Budgets alerts,
which must be created by hand. **Budgets alerts are notifications, not caps.** The account's
Lambda concurrency quota is not a quota on spend.

Idempotency: processing claims an item with a conditional update
(`QUEUED → PROCESSING`, or an expired lease). A second invocation for the same
item does nothing. A photo or slip whose stored result already matches the
object's SHA-256 is not sent to Bedrock or Textract again.

## 17. Frontend integration

- Module: `frontend/src/features/judge-trial/`; root component
  `JudgeTrial` (default export of `index.ts`), standalone page
  `frontend/trial.html`.
- API base: the same `VITE_API_BASE_URL` as the rest of the app. With no API
  configured the feature shows "Trials need the SiltProof API" and does
  nothing; it has no offline fake.
- The client sends `Authorization: Bearer` on every trial call, uploads with
  `FormData` to the presigned POST, and polls `GET /trials/{id}` every 2.5 s
  while anything is `QUEUED` or `PROCESSING`.
- CORS: the HTTP API must allow `Authorization` and `Content-Type` and the
  methods `GET, POST, PUT, DELETE, OPTIONS`; the evidence bucket must allow
  `POST` from the site's origin.

## 18. The Kolkata field case

Same API, same data model, `kind: "field"`:

1. `POST /trials` with `{ "kind": "field", "label": "New Town channel, 9 Oct 2026" }`.
2. `PUT …/details`: `workWindow` = 9 Oct 2026 IST; `drainLocation` with
   `source: "field_approximate"`, a `line` approximated from the walk, a
   tolerance of tens of metres, and `derivedFromPhotos: true` if the line came
   from the same photos (R1 then reports `INCONCLUSIVE`, because checking
   photos against a line drawn through them proves nothing).
3. Upload the 11 originals as `role: "current"`. Up to 15 MB each, 12 photos,
   120 MB per trial — the largest original (7.8 MB) fits.
4. No slips, traces, bill or disposal site: those checks are
   `NOT_EVALUATED`. Nothing names an owner, a contractor or Dhapa.

The original photographs are never committed to this repository.
