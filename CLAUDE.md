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
backend/
  ingest/    S3 event -> EXIF, pHash, Textract, Bedrock -> DynamoDB
  api/       routes, verification rules R1-R10, decisions
  events/    sam local invoke payloads
data/        osm_drains, gen_slips, gen_trips, seed, reset + simulated/
scripts/     check_region.py
frontend/    React app
```

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
  do not throttle during a seed.
- Small, clear commits.

## Verdict vocabulary

Trip: any hard-rule fail → `HOLD`; else any soft fail → `REVIEW`; else
`VERIFIED`. Drain: red if any trip is held, amber if any is in review, else
green. Rate is ₹1,800/tonne (illustrative).
