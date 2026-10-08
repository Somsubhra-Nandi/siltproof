# SiltProof — conventions for future sessions

A ward engineer approves or holds payment per drain section in a contractor's
desilting bill. One ward, one bill, 18 drains, ~120 trips. Hackathon MVP for a
demo, not production: keep code simple and readable.

## The plan file is the source of truth

`siltproof-hackathon-plan.md` defines scope, architecture, the data model, the
10 verification rules, the dataset spec and the day-by-day schedule. Read it
before changing anything structural. If this file and the plan disagree, the
plan wins.

## Out of scope — do not build

Login or auth (one hard-coded engineer), a contractor portal or mobile app,
multiple wards/cities/bills, real-time GPS streaming, model training.

**No extra AWS services.** Step Functions, SQS, Cognito, SageMaker and
microservices are all out. One ingest Lambda and one api Lambda is enough.

## Stack

- **Infra:** AWS SAM (`infra/template.yaml`), region `ap-south-1`
- **Backend:** Python 3.12 Lambdas, arm64 — `backend/ingest` (S3-triggered),
  `backend/api` (HTTP API)
- **AI:** Textract `AnalyzeDocument` with QUERIES for slips; Bedrock
  (`BedrockModelId` parameter, an `in.*` inference profile by default) for photo
  vision and evidence summaries
- **Data:** one DynamoDB table `siltproof`, `pk`/`sk` strings, on-demand
- **Frontend:** React + Vite + TypeScript + MapLibre GL, Amazon Location Maps v2
  style via API key, hosted on Amplify

## Folder layout

```
infra/       SAM template + samconfig.toml.example
backend/     CodeUri for BOTH functions, so they share common/
  common/    provider layer: textract, bedrock, location, photo, store,
             geo, retry, config + fixtures/ for mock mode
             rules.py    the ten verification rules, pure functions
             billdata.py assembles a bill from DynamoDB + S3
  ingest/    S3 event -> EXIF, pHash, Textract, Bedrock -> DynamoDB
  api/       routes: verify, bill, drain, decision, summary, upload-url
  layers/    photo deps (pillow, imagehash, numpy, scipy) for ingest only
  events/    sam local invoke payloads
data/        osm_drains, gen_trips, gen_slips, gen_photos, check_photos,
             seed, reset; dataset.py holds the shared constants
  out/       generated output, git-ignored
scripts/     check_region.py, make_demo_fixtures.py
tests/       offline pytest suite (moto + MOCK_AWS=1)
frontend/    React app; src/__tests__ runs under vitest + jsdom
             public/data/demo is a generated API snapshot for offline use
```

Handlers are `ingest.app.lambda_handler` and `api.app.lambda_handler`; both
import `from common import ...`.

## Rules

- **Never commit secrets.** API keys, account ids and credentials go in `.env`
  (git-ignored); only `.env.example` is tracked, with placeholder values.
- **Ask before any command that creates AWS resources or costs money** —
  `sam deploy`, `sam sync`, seeding, anything that calls Bedrock or Textract in
  bulk. `scripts/check_region.py` is the one cheap exception and still needs a
  heads-up.
- Heavy AI runs at **ingestion** (S3-triggered), never on page load and never
  inside `POST /verify`, which stays fast deterministic rules only.
- Keep the ingest Lambda's reserved concurrency low (3) so Bedrock and Textract
  do not throttle during a seed. It is the `IngestReservedConcurrency`
  parameter, 0 by default because the account's quota of 10 forbids any
  reservation (DECISIONS.md).
- Small, clear commits.
- **Every script that can reach AWS defaults to not reaching it.** `seed.py`,
  `reset.py` and `gen_trips.py` are dry-run/offline unless `--live` is passed,
  and `--live` prints the call plan and a billable-call estimate first. Keep it
  that way for anything new.
- Run the tests before committing: `.venv/bin/python -m pytest` (249 tests,
  about 80 s) and `cd frontend && npm test` (17). No AWS account needed.
  `sam build` needs `--use-container`, because the local python is 3.13 and
  the runtime is 3.12.
- Rules live in `backend/common/rules.py` as pure functions over dicts. Keep
  them that way: the API assembles the context, the rules only judge it.
- After changing rules or generators, regenerate the offline snapshot with
  `python scripts/make_demo_fixtures.py`, or the frontend demo will drift.
- `data/out/ground_truth.json` is the expected output of rules R1-R10 for the
  generated dataset. Day 2's rules are tested against it; if a rule disagrees,
  one of the two is wrong - decide which before changing either.
- Mock mode (`MOCK_AWS=1`) must keep using the same parsers as live mode. If
  you add a provider call, add a fixture for it rather than branching the
  parsing.

## Verdict vocabulary

Trip: any hard-rule fail → `HOLD`; else any soft fail → `REVIEW`; else
`VERIFIED`. Drain: red if any trip is held, amber if any is in review, else
green. Rate is ₹1,800/tonne (illustrative).

Seeded totals: claimed 1,240 t · verified 805 t · review 65 t · **hold 370 t =
₹6.66 lakh**. Approving the two review drains on camera takes verified to
870 t, which is the plan's headline. Two deviations from plan section 5 are
deliberate and recorded in DECISIONS.md: **R1 has a soft band** (30–60 m from
the drain is a soft fail, beyond 60 m hard) so drain 8 reads amber, and drain
11 is red rather than the plan's "🟡/🔴".
