# SiltProof

> We don't show the city its drains. We tell the engineer exactly how much of the
> desilting bill to pay, and show the evidence for every rupee we hold.

A ward storm-water engineer has one contractor bill to approve two weeks before
the monsoon: 18 drain sections, ~120 dumper trips, ₹1,800 a tonne. SiltProof
ingests the evidence (photos, weighbridge slips, GPS traces), extracts the facts
with AWS, runs 10 deterministic rules, and colours a ward map green, amber or
red — so the engineer can hold the exact amount that the evidence does not
support.

Built for Environmental Hacks (AWS), 8–10 Oct 2026. Full scope and schedule in
[`siltproof-hackathon-plan.md`](siltproof-hackathon-plan.md), which is the
source of truth.

**Data statement:** contractor bills are not public. The drain geometry and the
photos (with their GPS and timestamps) are real; weighbridge slips and GPS
traces are simulated. Every check runs live on AWS.

## Architecture

```
seed.py / live upload ──▶ S3 evidence bucket (photos/ slips/ traces/)
                                     │ S3 event
                                     ▼
                            Lambda: ingest
                   EXIF + pHash · Textract QUERIES · Bedrock vision
                                     │
                                     ▼
                         DynamoDB (single table)
                                     ▲
                                     │
        API Gateway HTTP API ──▶ Lambda: api  (rules R1–R10, decisions)
                                     ▲
                                     │
          React + MapLibre (Amazon Location tiles) on Amplify Hosting
```

| Service | Job |
|---|---|
| S3 | Evidence store |
| Lambda | `ingest` (extraction) and `api` (rules + endpoints) |
| Textract | Reads weighbridge slips with Queries |
| Bedrock | Before/after photo check and the evidence summary |
| Amazon Location | Map tiles, and routes for generating dumper traces |
| DynamoDB | Drains, trips, extracted facts, verdicts, decisions |
| API Gateway | HTTP API for the frontend |
| Amplify Hosting | Hosts the React app |
| SAM | Infrastructure as code |

## Repository layout

```
infra/       SAM template and samconfig example
backend/     packaged as one tree, so both functions share common/
  common/    Textract / Bedrock / Location providers, EXIF + pHash,
             DynamoDB access, geodesy, retries, and mock fixtures
  ingest/    S3-triggered evidence extraction
  api/       HTTP API endpoints
  common/rules.py   the ten verification rules
  layers/    photo dependencies, attached to the ingest function only
  events/    payloads for `sam local invoke`
data/        generators (drains, trips, slips, photos), seed.py, reset.py
scripts/     check_region.py
tests/       offline test suite
frontend/    React + Vite + MapLibre app
```

## Status

Days 1 and 2 complete, entirely offline. The evidence pipeline, the ten
verification rules, the API behind them and the ward engineer's decision
screen are all written and tested: **249 backend tests and 17 frontend tests
pass with no AWS account**.

Still to come: the live upload moment (Day 3), UI polish, and the video.
`POST /upload-url` is implemented but has never been exercised against real
S3.

**Nothing has been deployed** — the AWS account is still pending activation.
See MORNING.md for the ordered commands once it is live. Until then the app
runs off an offline snapshot (below), so the whole flow can be clicked
through.

### Try it right now, with no AWS

```bash
python scripts/make_demo_fixtures.py     # drives the pipeline through moto
cd frontend && npm install && npm run dev
```

With `VITE_API_BASE_URL` unset the app reads the generated snapshot in
`public/data/demo`: the map colours itself from real rule output, the
drill-down shows the real evidence, and approving the two amber drains moves
verified from 805 t to 870 t with ₹6.66 lakh still held.

**The map works with no AWS at all.** Without an Amazon Location key it draws a
bundled style built from `public/data/basemap.geojson` — the ward's real roads
and water from OpenStreetMap, fetched once by `data/osm_basemap.py` — and shows
a small "Offline map (dev)" badge. No tile server, no network request. With
`VITE_AWS_REGION` and `VITE_LOCATION_API_KEY` set it uses Amazon Location
exactly as it always did, and falls back automatically if that style fails.

Useful query parameters while working on the screen:

| | |
|---|---|
| `?state=verified` | skip the pre-verification view |
| `?drain=14` | open a drain straight away |
| `?style=<url>` | override the basemap style (point it at a missing file to test the fallback) |

See `frontend/screenshots/` for what it looks like, or regenerate them with
`npm run preview` and `npm run screenshot`.

## Commands

### 1. Check the region (do this first)

One tiny live call each to Bedrock, Textract and Amazon Location. Creates no
resources; costs a fraction of a cent.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r scripts/requirements.txt

python scripts/check_region.py ap-south-1
# or with a different model:
python scripts/check_region.py ap-south-1 --model-id in.anthropic.claude-sonnet-5
```

`scripts/requirements.txt` pins `boto3[crt]` on purpose: without the `crt`
extra, boto3 cannot read credentials created by `aws login`.

If anything FAILs, try `us-east-1` before building on the region.

### 2. Deploy the backend

```bash
cp infra/samconfig.toml.example infra/samconfig.toml   # once

# --use-container is required: the local python is 3.13, the runtime 3.12
sam build --use-container --template infra/template.yaml

# First time (interactive, pick region ap-south-1):
sam deploy --guided --template infra/template.yaml

# After that:
sam deploy --config-file infra/samconfig.toml
```

Note the stack outputs: `ApiUrl`, `EvidenceBucketName`, `TableName`.

Run a Lambda locally without deploying:

```bash
sam local invoke ApiFunction --event backend/events/get-bill.json --template infra/template.yaml
sam local invoke IngestFunction --event backend/events/s3-photo.json --template infra/template.yaml
sam local start-api --template infra/template.yaml      # serves on :3000
```

### 3. Build the dataset (all offline, no AWS)

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt

# 18 drain geofences from OpenStreetMap for your ward
python data/osm_drains.py --center <lat,lon> --radius 2500 \
                          --dumpsite <lat,lon>
# ...or, with no ward chosen yet:
python data/osm_drains.py --synthetic --center <lat,lon>

python data/gen_trips.py          # 117 trips, traces, ground_truth.json
python data/gen_slips.py          # a weighbridge slip per trip
python data/gen_photos.py         # stand-in photos with real EXIF
python data/osm_basemap.py        # roads and water for the offline map

cp data/out/drains.geojson data/out/dumpsite.geojson frontend/public/data/
```

Check the real photos before seeding them:

```bash
python data/check_photos.py ~/siltproof-photos
```

### 4. Seed

```bash
python data/seed.py                                   # dry run, writes a plan
python data/seed.py --live --bucket <EvidenceBucketName>   # for real
python data/reset.py --live --bucket <EvidenceBucketName>  # between takes
```

### 5. Tests

```bash
python -m pytest                 # 249 backend tests, about 80 s, entirely offline
cd frontend && npm test          # 46 component and unit tests
cd frontend && npm run screenshot  # four views of the offline dashboard
```

`tests/test_rules_oracle.py` is the acceptance test for rules R1-R10: it
generates the dataset, seeds it into moto, ingests it, verifies the bill and
compares all 117 trips against `data/out/ground_truth.json`.

### 6. Run the frontend

```bash
cd frontend
cp .env.example .env        # then fill in VITE_LOCATION_API_KEY and VITE_API_BASE_URL
npm install
npm run dev                 # http://localhost:5173
npm run build               # type-check + production build
```

## Manual steps (not automated)

These need a human in the AWS console or CLI:

1. **`aws configure`** — credentials for an IAM user/role with permission to
   deploy SAM stacks, plus `bedrock:InvokeModel`, `textract:AnalyzeDocument` and
   `geo-routes:CalculateRoutes` so `check_region.py` can run.
2. **Bedrock model access** — Bedrock console → *Model access* in your region →
   request access to the Claude model you intend to use. Approval can take time,
   so do it before anything else, and keep a second model enabled as a fallback.
   Pass the model (or inference profile) id to `check_region.py` and to the
   `BedrockModelId` stack parameter.
3. **Amazon Location API key** — Location console → *API keys* → create a key
   scoped to Maps only (style descriptor, tiles, glyphs, sprites) in the same
   region, with referrers restricted to the app's origins. Put it in
   `frontend/.env` as `VITE_LOCATION_API_KEY`. It is a browser key, so never
   commit it. Full steps, Amplify variables and checks:
   [docs/amazon-location.md](docs/amazon-location.md).
4. **AWS Budget alarm** — Billing console → *Budgets* → a small monthly budget
   with an email alert, before any seeding.
5. **Amplify Hosting** — Amplify console → *Host web app* → connect this Git
   repo, app root `frontend`, build `npm run build`, output `dist`, and add the
   `VITE_*` variables as Amplify environment variables.
6. **Request `geo-routes` permission** if `check_region.py` reports a FAIL for
   location — the route calculation needs `geo-routes:CalculateRoutes`.

Nothing in this repo deploys or seeds automatically. `sam deploy` and the seed
scripts are always run by hand.
