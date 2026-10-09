# Judge trial ("Try SiltProof yourself"): handoff

Written 9 Oct 2026. Everything needed is on GitHub; nothing depends on the
machine it was written on.

## Branch

- **Branch:** `feat/judge-self-service`, from `feat/hybrid-frontend` at
  `4474ba4`. Not merged anywhere. Not deployed.
- **Tip:** `git log -1 origin/feat/judge-self-service` (this file is in the
  last commit, so it cannot quote its own SHA). The implementation commits are
  listed by `git log --oneline 4474ba4..origin/feat/judge-self-service`.
- The 18-drain investigation, Bill B1, `rules.py` and the app shell are
  unchanged. The only edits to existing files are small and additive (below).

## API contract

`docs/JUDGE-TRIAL-API.md` is the contract: IDs, token access, ten routes,
schemas, upload flow, states, check statuses, errors, isolation, retention,
quotas, frontend use and the Kolkata field case. It matches the code.

| Method | Path |
|---|---|
| POST | `/trials` |
| GET / DELETE | `/trials/{trialId}` |
| PUT | `/trials/{trialId}/details` |
| POST | `/trials/{trialId}/upload-url` |
| POST | `/trials/{trialId}/evidence/{evidenceId}/complete` |
| POST | `/trials/{trialId}/evidence/{evidenceId}/retry` |
| DELETE | `/trials/{trialId}/evidence/{evidenceId}` |
| POST | `/trials/{trialId}/analyze` |
| GET | `/trials/{trialId}/results` |

## What was built

**Backend** (`backend/trial/`, `backend/api/trials.py`):

- trial creation with server-generated IDs and a hashed 256-bit bearer
  token; optional invite code; 48 h expiry with DynamoDB `ttl`;
- presigned **POST** uploads to `trials/{id}/originals/{evidenceId}.ext`
  (size range and content type enforced by S3), then server checks of size,
  type and file signature at `…/complete`;
- photo processing in the ingest Lambda: EXIF (GPS, time, offset presence,
  GPS accuracy tag, camera with NUL padding stripped, orientation) and pHash
  from the original; Bedrock through `common.bedrock.check_photo`; a
  traceable processing copy only above 8 MB / 8,000 px;
- slip processing: Textract `AnalyzeDocument` QUERIES through
  `common.textract`; JPEG, PNG, single-page PDF (multi-page refused before the
  call);
- trace validation (two formats, documented) and bill storage, inline in
  the API;
- hard daily and per-trial quotas (conditional DynamoDB counters), idempotent
  processing (lease + "same bytes already read" skip), bounded retries;
- analysis over 14 checks (R1–R10, GPS_GAP, T1–T3) with PASS / CONSISTENT /
  REVIEW / FAIL / INCONCLUSIVE / NOT_EVALUATED; template summary; provenance;
- delete-now for a whole trial or one file.

**Frontend** (`frontend/src/features/judge-trial/`, `frontend/trial.html`):
five screens (new trial with privacy notice; six evidence groups with map,
uploads with progress, per-field server errors; analysis with polling;
results with findings, not-evaluated checks, AI observations, slip fields with
confidence, original previews and provenance; start another / delete).

**Edits to existing files** (all additive):

| File | Change |
|---|---|
| `backend/api/app.py` | 4 lines: forward `/trials…` route keys to `api/trials.py` |
| `backend/ingest/app.py` | 7 lines: handle `{"trialProcess": …}` invocations |
| `backend/common/mocks.py` | trial keys get MOCK-labelled fixtures (2 new fixture files) |
| `infra/template.yaml` | trial routes, params, env, invoke policy, TTL, lifecycle, CORS, route throttles |
| `frontend/vite.config.ts` | builds `trial.html` as a second page |
| `DECISIONS.md` | one new section |

## Six evidence groups: status

| Group | End to end offline (moto + mocks) | Live AWS |
|---|---|---|
| 1 Contractor bill: manual claim + stored document | yes (API, browser) | not run |
| 2 Photos: EXIF, pHash, Bedrock | yes; Bedrock **mocked** | not run |
| 3 Weighbridge slips: Textract | yes; Textract **mocked** | not run |
| 4 Truck GPS trace | yes (no AI involved) | not run |
| 5 Drain location (typed, map click, photo-derived line) | yes | n/a |
| 6 Disposal site (point + radius; polygon via API) | yes | n/a |

All six were exercised together in the API tests
(`test_all_six_groups_end_to_end`) and in the headless browser run. **No
live Bedrock, Textract, S3 or DynamoDB call has been made.** Mock-mode
readings are labelled "OFFLINE MOCK" everywhere, and checks computed from them
are reported NOT_EVALUATED.

## Not done / incomplete

- Nothing has run against real AWS. Real Nova Pro output on real 7.7 MB phone
  photos, and real Textract on a real slip PDF, are untested.
- No bill extraction (by design: SiltProof has none). The claim is typed.
- Disposal-site **polygon** and drain **line** entry are API-only, except the
  "approximate the channel from the photo GPS fixes" shortcut; the UI takes a
  point + radius for the site and a point for the drain.
- No scheduled cleanup job: retention relies on S3 lifecycle (2 days) and
  DynamoDB TTL, both asynchronous, plus delete-now.
- Not linked from the investigation yet (one link, below).
- The summary is a fixed template; no Bedrock text summary for trials.
- Orphan risk: deleting a trial while a photo is mid-processing can leave its
  processing copy until the lifecycle rule removes it.

## Supported uploads

| Group | Types | Per file | Per trial |
|---|---|---|---|
| photo | JPEG, PNG | 15 MB | 12 |
| slip | JPEG, PNG, PDF (1 page) | 10 MB | 6 |
| bill | PDF, JPEG, PNG | 10 MB | 2 |
| trace | JSON (`{"points":[{lat,lon,t}]}`), GeoJSON LineString + `properties.times` | 2 MB | 4 |

At most 24 files and 120 MB per trial. GPX, CSV, HEIC, WebP: refused.

## Frontend integration (one small change)

The feature does not touch `App.tsx`, `main.tsx` or any shared component.
Two ways to expose it:

1. **Link to the standalone page** (recommended, zero coupling). In the
   masthead or bill meta of `BillSheet.tsx`:

   ```tsx
   <a className="link" href="/trial.html">Try SiltProof yourself</a>
   ```

   `trial.html` is built by `npm run build` (see `vite.config.ts`) and is
   served by Amplify as a static page. Its "Explore the investigation" link
   goes back to `/`.

2. **Mount the component** inside the app instead:

   ```tsx
   import JudgeTrial from './features/judge-trial'
   // render <JudgeTrial investigationHref="/" /> for e.g. ?view=trial
   ```

   It brings its own CSS (`trial.css`, scoped under `.jt`) and uses the
   tokens and `.btn`, `.link`, `.rid`, `.spinner`, `.skeleton` classes from
   `src/index.css`, and `../../basemap` for the map style.

Requirements:

- **Env vars:** the existing `VITE_API_BASE_URL` (empty → the feature shows
  "Trials need the SiltProof API" and does nothing), and optionally
  `VITE_MAP_CENTER_LAT` / `VITE_MAP_CENTER_LON` (defaults to New Town,
  Kolkata when unset) and the existing Amazon Location variables. No new
  variables, no feature flag beyond whether the link is shown.
- **No offline fake.** Offline mode keeps the prepared demo; trials need an
  API (deployed, or the local dev server below).
- The token lives in `sessionStorage` for that tab only.

## Backend deployment requirements (need approval; not done)

`sam build --use-container --template infra/template.yaml && sam deploy --config-file infra/samconfig.toml`
creates or changes:

- **API routes:** the ten trial routes on the existing HTTP API, with
  per-route throttling; CORS gains `Authorization`, `PUT`, `DELETE`.
- **ApiFunction:** env `TRIAL_PROCESSOR_FUNCTION` (the ingest function) and
  `TRIAL_INVITE_CODE`; new `LambdaInvokePolicy` on the ingest function.
- **Globals env:** `TRIAL_DAILY_TRIALS`, `TRIAL_DAILY_BEDROCK_CALLS`,
  `TRIAL_DAILY_TEXTRACT_CALLS`.
- **Evidence bucket:** CORS adds `POST`; lifecycle rule `ExpireJudgeTrials`
  (prefix `trials/`, 2 days, abort multipart after 1 day).
- **Table:** `TimeToLiveSpecification` on `ttl`. B1 items have no `ttl` and
  are unaffected.
- **New parameters:** `TrialInviteCode` (NoEcho, default empty),
  `TrialDailyTrials` (60), `TrialDailyBedrockCalls` (200),
  `TrialDailyTextractCalls` (100).
- No new functions, buckets, tables or services. IngestFunction already has
  S3, DynamoDB, Textract and Bedrock permissions.

Before making it public:

1. Set `TrialInviteCode` and give it to judges in person or in the submission
   notes, not in the frontend bundle.
2. Create an **AWS Budgets** alert (notification only, not a cap).
3. Consider restricting the bucket and API CORS origins to the Amplify domain.
4. **Disable or protect the existing B1 `POST /upload-url`.** It is
   unauthenticated, and its uploads under `photos/live/` trigger the ingest
   Lambda and a Bedrock call with no quota. This predates the trial feature
   and was not changed here.
5. Run one approved live smoke test: create a trial, upload one generated
   photo and one slip, check `vision.modelId` is set and `mocked` is false,
   check the Bedrock counter moved, delete the trial.

## Security controls, and what remains

In place: unguessable IDs; hashed bearer token, constant-time compare,
uniform 404; server-generated keys only; presigned POST with size and type
conditions, 300 s; server re-validation of size, type, signature; per-group,
per-trial and byte limits as conditional writes; daily and per-trial Bedrock
and Textract allowances reserved before each call (fail closed); per-trial
analysis cap; idempotent processing; bounded retries; private bucket;
5-minute preview links only for the trial's own originals; TTL + lifecycle +
delete-now; privacy notice with acknowledgement.

Unresolved risks:

- The token is a capability: anyone who gets it (shared screen, copied
  storage) can read and change that trial until it expires.
- With no invite code, anyone can create up to the daily trial count, and
  each trial up to its allowance, until the daily Bedrock/Textract caps are
  hit. The caps bound cost; they do not prevent a nuisance user exhausting
  them for judges. Set the invite code.
- CORS is `*` on the bucket and API.
- Real-service behaviour (Nova on very large images, Textract on real slips)
  is unverified.
- The existing B1 `/upload-url` (above).

## Tests and results (9 Oct 2026, this branch)

| Check | Result |
|---|---|
| `.venv/bin/python -m pytest` | **446 passed** (319 existing + 127 new: `tests/test_trials_api.py`, `tests/test_trial_rules.py`) |
| `cd frontend && npm test` | **97 passed** (76 existing + 21 new) |
| `npm run lint` | clean |
| `npm run build` | builds `index.html` and `trial.html` |
| `cfn-lint infra/template.yaml` | clean |
| `node scripts/check-trial.mjs` (headless Chrome, dev server) | 19 / 19 checks pass; screenshots in `frontend/screenshots/trial/` |

The new backend tests cover creation, unique IDs, token checks on every
route, cross-trial refusal, expiry, invite code, upload scope and policy,
unsupported types, size and count limits, signature and size mismatches,
EXIF preservation, NUL-stripped camera, PNG without GPS, missing EXIF,
unreadable images, processing copies, Bedrock and Textract failures,
multi-page PDF, quota exhaustion (daily and per trial), the conditional-write
shape of every reservation, the stale-reader case, idempotent re-delivery,
leases, bounded retries, trace validation (12 malformed cases), inconclusive
and not-evaluated results, the six-group run, the Kolkata-style field case,
deletion and TTL, and B1 isolation (B1 items and objects unchanged; trial
writes only under `TRIAL#` / `QUOTA#` and `trials/`; trial keys ignored by
the S3 ingest; the template's notification prefixes exclude `trials/`).

## Run it locally (no AWS)

```bash
git fetch origin && git checkout feat/judge-self-service
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt "moto[server]"
python -m pytest                                   # backend, ~3-4 min

python scripts/trial_dev_server.py &               # API :3001, moto :5005, MOCK_AWS=1
cd frontend && npm ci
VITE_API_BASE_URL=http://127.0.0.1:3001 npm run dev
# open http://localhost:5173/trial.html

# optional headless check (needs Chrome):
python ../scripts/make_trial_fixtures.py /tmp/trial-fixtures
CHROME_PATH=/path/to/chrome node scripts/check-trial.mjs /tmp/trial-fixtures
```

The dev server keeps everything in memory, uses fixtures labelled OFFLINE
MOCK for Textract and Bedrock, and processes files on a background thread
after 1.5 s so the queued/processing states and polling show.

## Environment variable names

Frontend: `VITE_API_BASE_URL`, `VITE_MAP_CENTER_LAT`, `VITE_MAP_CENTER_LON`,
`VITE_AWS_REGION`, `VITE_LOCATION_API_KEY`, `VITE_LOCATION_MAP_STYLE`,
`VITE_LOCATION_COLOR_SCHEME`.

Backend (set by the template): `TRIAL_PROCESSOR_FUNCTION`,
`TRIAL_INVITE_CODE`, `TRIAL_DAILY_TRIALS`, `TRIAL_DAILY_BEDROCK_CALLS`,
`TRIAL_DAILY_TEXTRACT_CALLS`; optional `TRIAL_MAX_BEDROCK_PER_TRIAL`,
`TRIAL_MAX_TEXTRACT_PER_TRIAL`, `TRIAL_MAX_ANALYSES`. Existing:
`TABLE_NAME`, `EVIDENCE_BUCKET`, `BEDROCK_VISION_MODEL_ID`, `MOCK_AWS`.

## Kolkata field case

Use the same API (`docs/JUDGE-TRIAL-API.md` section 18): a `field` trial,
work window 9 Oct 2026 IST, the 11 originals as `current`, a
field-approximate drain line (the UI can draw it from the photo fixes; it is
then marked `derivedFromPhotos`, so R1 reports INCONCLUSIVE). The originals
are not in the repository and must not be added.

## Recommended next steps

1. Review this branch (`git diff 4474ba4..origin/feat/judge-self-service`).
2. Add the one link from the investigation to `/trial.html`.
3. With approval: deploy, set the invite code, add a Budgets alert, run the
   one-trial live smoke test above, then run the Kolkata field trial.
4. Decide what to do about the unauthenticated B1 `POST /upload-url`.
5. Merge into `feat/hybrid-frontend` (or main) once the team agrees.
