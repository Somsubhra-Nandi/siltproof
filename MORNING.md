# Morning handover — Day 1 night session

Everything below was built and tested with **no AWS calls at all**. The account
was still pending activation, so nothing was deployed, uploaded or invoked.

**145 tests pass in 28 s.** `.venv/bin/python -m pytest`

---

## 1. What is built

| Piece | State |
|---|---|
| `backend/common/` provider layer | **Done.** Textract, Bedrock, Location, EXIF + pHash, DynamoDB, geodesy, retries. Real and mock modes share one parsing path. |
| `backend/ingest/` | **Done.** Slips via Textract QUERIES, photos via EXIF + pHash + Bedrock vision, traces summarised. Idempotent, throttle-retrying, failures recorded as items. |
| `data/osm_drains.py` | **Done.** Overpass or `--synthetic`, 30 m buffers, no city hard-coded. |
| `data/gen_trips.py` | **Done.** 117 trips, 30 s traces, every planted case, `ground_truth.json`. |
| `data/gen_slips.py` | **Done.** One slip per trip, fictional weighbridge, a quarter degraded. |
| `data/gen_photos.py` | **Done.** 40 stand-in JPEGs with real EXIF GPS and timestamps. |
| `data/check_photos.py` | **Done.** Pre-seed EXIF/geofence check for the real shoot. |
| `data/seed.py`, `data/reset.py` | **Done.** Dry-run by default; `--live` prints the call plan first. |
| Frontend map | **Done for Day 1.** 18 drain polygons + dump site, fitted bounds, all neutral grey. |
| Tests | **Done.** 145, offline, including the Day 1 exit check end to end. |

### Still stubbed, as planned

- **The API Lambda returns hard-coded JSON.** All six routes exist and respond,
  but nothing reads DynamoDB yet. That is Day 2 morning.
- **Rules R1–R10 are not written.** `data/out/ground_truth.json` is the oracle
  they must reproduce — expected verdict and rule ids for all 117 trips.
- **No verdict colours, drill-down panel, Run Verification button, or decision
  buttons.** Day 2.
- **No live upload** (`POST /upload-url` returns nulls). Day 3.
- **No Bedrock evidence summary** wired to the API. The provider call
  (`bedrock.summarise`) exists and is tested; the route is not.

### Two things Day 2 must honour

1. **R1 needs a soft band.** Plan §6 wants drain 8 amber, but §5 calls R1 hard.
   The dataset puts drain 8's photo 45 m out, and `ground_truth.json` expects a
   **soft** fail. Rule: inside the 30 m buffer passes, 30–60 m is soft, beyond
   60 m is hard. Without the band, drain 8 comes out red and the demo's "not
   every flag is fraud" beat is lost.
2. **The totals.** Seeded: claimed 1,240 t, verified 805 t, review 65 t,
   **hold 370 t = ₹6.66 lakh**. Approving the two review drains on camera takes
   verified to **870 t** — the plan's headline. Both numbers in the §10 video
   script are exact. Reasoning in DECISIONS.md.

---

## 2. Test results

```
tests/test_textract_parser.py      40 passed   field parsers + whole slips, incl. garbled
tests/test_bedrock_parser.py       24 passed   forced tool output + every malformed shape
tests/test_ingest_handler.py       20 passed   moto S3 + DynamoDB, idempotency, failures
tests/test_generators.py           19 passed   geometry, Overpass parsing, planted cases
tests/test_e2e_offline.py          15 passed   the Day 1 exit check, generators -> ingest
tests/test_location_and_safety.py  14 passed   route parsing + the dry-run rails
tests/test_photo_evidence.py       13 passed   EXIF, missing EXIF, pHash separation
                                  ---
                                  145 passed in 28s
```

Also verified, outside pytest:

- `sam validate --lint` → valid.
- `sam build --use-container` → builds; function artifacts 156 KB each, photo
  layer 204 MB.
- Ran the **built artifact inside `public.ecr.aws/lambda/python:3.12-arm64`**:
  numpy, scipy, Pillow and imagehash all import on aarch64, pHash computes, and
  both handlers import and answer.
- `npm run build` and `npm run lint` → clean; `drains.geojson` and
  `dumpsite.geojson` serve correctly from the built app.

### Nothing failed. Four bugs were found and fixed

Each was silent and would have cost time later:

1. `parse_weight_tonnes("not a weight")` returned **0.0 tonnes** — the OCR
   corrector turned its letter `o` into a zero.
2. `parse_vehicle_no("MH O1 AB l234")` returned `L234` — upper-casing ran
   before the digit fix.
3. `split_long_lines` gave its last drain section every leftover point, so one
   section came out several times longer than the rest.
4. The first photo generator drew every scene from one template, and **all 40
   photos hashed within 2 bits of each other** — R3 would have called every
   photo a duplicate. Scenes now vary; measured separation is exact copy 0,
   edited copy 6, nearest unrelated pair 16, threshold 12.

### What could not be verified offline

- **No live AWS call was made.** The fixtures are written from the documented
  API shapes. The three that meet reality first are Textract QUERIES block
  relationships, the Bedrock Converse `toolUse` block, and `geo-routes` leg
  geometry. The route parser is deliberately written to *search* for the
  geometry rather than assume a path, because that shape is the least certain.
- **The map has never rendered against a real Amazon Location style** — that
  needs the API key. The GeoJSON layers are standard MapLibre and the assets
  serve correctly, but the style URL is unproven.
- **`sam deploy` has never run.** The template lints and builds; CloudFormation
  has not seen it.
- Re-measure the pHash threshold after the real shoot: photos of the same drain
  from slightly different angles may sit closer together than the generated
  ones do.

---

## 3. Run these, in this order

### Step 0 — is the account awake?

```bash
cd ~/Desktop/aws-environmental-hacks/siltproof
source .venv/bin/activate
python scripts/check_region.py ap-south-1
```

Expect `PASS` on all three. If you still get `SubscriptionRequiredException`,
the account is **still not activated** — nothing below will work. Check the
Billing console for a valid payment method and wait.

### Step 1 — budget alarm, before anything is seeded

Billing console → **Budgets** → monthly cost budget, say $20, email alert at
50% and 80%. One minute, and it is the only thing standing between a bug and a
surprise bill. Plan §9 lists this as a named risk.

### Step 2 — deploy

```bash
cp infra/samconfig.toml.example infra/samconfig.toml
sam build --use-container --template infra/template.yaml
sam deploy --guided --template infra/template.yaml
```

Accept `ap-south-1`, stack name `siltproof`, and the defaults for
`BedrockModelId` (`in.anthropic.claude-opus-5`) and `BedrockVisionModelId`
(`in.anthropic.claude-haiku-4-5-20251001-v1:0`). Allow IAM role creation; do
*not* save arguments to a committed file other than `infra/samconfig.toml`,
which is git-ignored.

Keep the three outputs:

```bash
export API_URL=$(aws cloudformation describe-stacks --stack-name siltproof \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' --output text)
export EVIDENCE_BUCKET=$(aws cloudformation describe-stacks --stack-name siltproof \
  --query 'Stacks[0].Outputs[?OutputKey==`EvidenceBucketName`].OutputValue' --output text)
export TABLE_NAME=siltproof
echo "$API_URL  $EVIDENCE_BUCKET"

curl -s "$API_URL/health"      # should report the model ids and bucket
```

### Step 3 — the real ward

Pick the ward, then:

```bash
python data/osm_drains.py --center <WARD_LAT,WARD_LON> --radius 2500 \
                          --dumpsite <DUMPSITE_LAT,DUMPSITE_LON>
```

It prints how many OSM ways it found. If fewer than 18 sections come back,
widen `--radius`. Then publish the geometry to the app:

```bash
cp data/out/drains.geojson data/out/dumpsite.geojson frontend/public/data/
```

### Step 4 — the photos

```bash
cp data/photo_map.example.csv data/photo_map.csv
# fill it in: filename,drainId,role,note   (see data/PHOTO_MAPPING.md)

python data/check_photos.py ~/siltproof-photos
```

Fix anything reported as "NO EXIF" before going further — a photo without EXIF
cannot be checked by R1 or R2. The mapping has to keep three planted cases
alive: drain 3 needs a **debris** load photo, drain 14's after photo must be
**the same file as drain 9's**, and drain 8 needs one shot 30–60 m off the
drain. `data/PHOTO_MAPPING.md` spells each out.

### Step 5 — regenerate the dataset on the real geometry

```bash
python data/gen_trips.py      # add --live to route with Amazon Location
python data/gen_slips.py
```

`gen_trips.py` refuses to finish if the totals drift off the plan. Skip
`gen_photos.py` if you are using real photos.

### Step 6 — seed

```bash
python data/seed.py                       # dry run first: read the plan
python data/seed.py --live --photos-dir ~/siltproof-photos \
                    --photo-map data/photo_map.csv
```

Start cheap if you want: add `--max-slips 20` for a first pass, then re-run
without it — already-ingested objects are skipped, so the second run only pays
for the new slips.

### Step 7 — verify, which is the Day 1 exit check

```bash
# evidence items extracted
aws dynamodb scan --table-name siltproof --select COUNT \
  --filter-expression 'begins_with(pk, :p)' \
  --expression-attribute-values '{":p":{"S":"EVID#"}}'

# anything that failed to ingest
aws dynamodb scan --table-name siltproof \
  --filter-expression '#s = :e' \
  --expression-attribute-names '{"#s":"status"}' \
  --expression-attribute-values '{":e":{"S":"ERROR"}}' \
  --query 'Items[].[s3Key.S,error.S]' --output table

# one photo, to see real Bedrock and EXIF output
aws dynamodb get-item --table-name siltproof \
  --key '{"pk":{"S":"EVID#photos/B1/drain3/load-01.jpg"},"sk":{"S":"PHOTO"}}'

# one slip, to see real Textract output
aws dynamodb get-item --table-name siltproof \
  --key '{"pk":{"S":"EVID#slips/B1/14-001.png"},"sk":{"S":"SLIP"}}'

# ingest logs
sam logs --stack-name siltproof --name IngestFunction --tail
```

Expected count: **number of photos + slips + traces uploaded** (274 with the
generated set). The error scan should return nothing.

### Step 8 — the app

```bash
cd frontend
cp .env.example .env     # VITE_LOCATION_API_KEY, VITE_API_BASE_URL=$API_URL,
                         # VITE_MAP_CENTER_LAT/LON = your ward
npm install && npm run dev
```

### Between recording takes

```bash
python data/reset.py --live     # clears verdicts and live uploads, keeps extractions
```

---

## 4. What a seed costs

| Call | Count | Rough list price |
|---|---|---|
| Textract `AnalyzeDocument` with QUERIES | 117 (one per trip's slip) | ~$1.76 |
| Bedrock Converse vision, Haiku 4.5 | 40 (one per photo) | ~$0.16 |
| Amazon Location `CalculateRoutes` | 18, only with `gen_trips.py --live` | ~$0.01 |
| `check_region.py` | 3 | under $0.01 |
| **A full first seed** | | **about $2** |

Re-seeding is close to free: the ingest Lambda skips any object whose S3 ETag
matches an already-successful item, so only changed or new evidence is sent to
Textract and Bedrock. `reset.py --live` (without `--hard`) keeps the extracted
facts for exactly that reason. `reset.py --live --hard` throws them away and
the next seed pays the full ~$2 again.

Day 2 adds the evidence summary: one Bedrock Opus call per flagged drain,
cached after the first click — about 6 calls, a few cents.

Running costs between demos are effectively zero: Lambda and DynamoDB are
on-demand, S3 holds about 14 MB.

---

## 5. Manual AWS steps still outstanding

1. **Account activation** — the blocker. Everything else waits on it.
2. **Budget alarm** — step 1 above.
3. **IAM for `siltproof-dev`** — the user needs, beyond deploy permissions
   (CloudFormation, S3, Lambda, IAM role creation, DynamoDB, API Gateway):
   - `bedrock:InvokeModel`
   - `textract:AnalyzeDocument`
   - `geo-routes:CalculateRoutes` (for `check_region.py` and `gen_trips --live`)
4. **Amazon Location API key** — Location console → *API keys* → scope it to
   `geo-maps:GetTile` and `geo-maps:GetStyleDescriptor` in `ap-south-1`. Goes
   in `frontend/.env` as `VITE_LOCATION_API_KEY`. Browser-visible, so never
   commit it. The map cannot render until this exists.
5. **Bedrock model access** — `get-foundation-model-availability` already
   reports Opus 5, Sonnet 5 and Haiku 4.5 as `AUTHORIZED` with entitlement
   `AVAILABLE` in `ap-south-1`, so this looks done; confirm after activation by
   running `check_region.py`.
6. **Amplify Hosting** — connect the repo, app root `frontend`, build
   `npm run build`, output `dist`, and add the `VITE_*` variables as Amplify
   environment variables.

---

## 6. Where things are

- `DECISIONS.md` — every choice made unattended, and why. Read the totals
  section and the R1 soft band before writing Day 2's rules.
- `data/PHOTO_MAPPING.md` — the mapping file format and the planted cases it
  must preserve.
- `data/out/ground_truth.json` — the expected output of R1–R10. Day 2's test
  oracle.
- `CLAUDE.md` — conventions for the next session.
- Nine commits tonight, smallest first, each one self-contained.
