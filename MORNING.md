# Handover

> **Day 2 update (8 Oct).** Rules R1-R10, the DynamoDB-backed API and the
> decision screen are built and tested offline. **249 backend tests and 17
> frontend tests pass.** The AWS account is still blocked, so nothing has been
> deployed and nothing has called AWS. The screen can be clicked through now
> using the offline snapshot: `python scripts/make_demo_fixtures.py`, then
> `cd frontend && npm run dev`.
>
> What changed since Day 1 is listed under "Day 2" below; the deployment
> sequence in section 3 is unchanged and still the thing to run first.

---

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

*(All of these were Day 1's gaps. Everything except the live upload is now
done — see the Day 2 section at the bottom.)*

- ~~The API Lambda returns hard-coded JSON.~~ **Done on Day 2.**
- ~~Rules R1–R10 are not written.~~ **Done on Day 2**, and checked against
  `ground_truth.json` trip by trip.
- ~~No verdict colours, drill-down, Run Verification or decision buttons.~~
  **Done on Day 2.**
- **No live upload.** `POST /upload-url` now returns a real presigned PUT, but
  nothing has exercised it against real S3 and there is no upload button in the
  UI yet. Day 3.
- ~~No Bedrock evidence summary wired to the API.~~ **Done on Day 2**, cached
  on the drain item.

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
`BedrockModelId` (`in.anthropic.claude-haiku-4-5-20251001-v1:0`) and `BedrockVisionModelId`
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


---

# Day 2 (8 October) — rules, API, decision screen

Still no AWS. The support case is open; everything below was built and tested
against moto and `MOCK_AWS=1`.

## What is built

| Piece | State |
|---|---|
| `backend/common/rules.py` | **Done.** R1–R10 plus the GPS-gap flag and four missing-evidence flags, as pure functions. |
| `POST /verify/{billId}` | **Done.** Runs the rules, persists trip and drain verdicts and the money summary. |
| `GET /bill/{billId}` | **Done.** Reads stored state; reports PENDING with nothing verified until a run happens. |
| `GET /drain/{drainId}` | **Done.** Trips, findings, both routes, slip fields with confidence, photos with the Bedrock verdict. |
| `POST /decision` | **Done.** Validates, records approve/hold with a note, re-summarises the bill. |
| `POST /drain/{id}/summary` | **Done.** One Bedrock call, cached on the drain item. |
| `POST /upload-url` | **Written, unexercised.** Real presigned PUT under `<prefix>/live/`, so `reset.py` can find it. No UI button yet. |
| Decision screen | **Done.** Coloured map, summary bar, Run Verification, drill-down, approve/hold. |
| Offline snapshot | **Done.** `scripts/make_demo_fixtures.py` → `frontend/public/data/demo`. |

## Test results

```
backend   249 passed in 82s        .venv/bin/python -m pytest
  test_rules_unit.py        49     each rule on its own, plus the awkward cases
  test_textract_parser.py   40     field parsers and whole slips
  test_api.py               35     routing, validation, decisions, caching, gaps
  test_bedrock_parser.py    24     forced tool output and every malformed shape
  test_ingest_handler.py    20     moto S3 + DynamoDB, idempotency, failures
  test_rules_oracle.py      20     all 117 trips against ground_truth.json
  test_generators.py        19     geometry, Overpass parsing, planted cases
  test_e2e_offline.py       15     the Day 1 exit check
  test_location_and_safety.py 14   route parsing and the dry-run rails
  test_photo_evidence.py    13     EXIF, missing EXIF, pHash separation

frontend   17 passed           cd frontend && npm test
sam validate --lint            valid
sam build --use-container      builds; both functions 212 KB
npm run build / npm run lint   clean
```

The oracle test was checked for bite: widening R8's tolerance to 90 minutes and
removing R1's soft band both make it fail loudly, naming the trips.

## Bugs found and fixed (none left open)

1. **One truck in two places at once, fifteen times.** Vehicles were assigned at
   random per drain, so the same truck was booked for overlapping trips 8 km
   apart. R6 exists to catch exactly that, so 30 extra trips would have been
   held and the totals would have been meaningless. Vehicles are now scheduled
   in time order against each truck's own timeline.
2. **The reused photos were dated before the originals.** R3 treats the earliest
   appearance of an image as genuine, so drain 9 would have been flagged and
   drain 14 cleared — the hero case backwards.
3. **`round(inf)` raised OverflowError.** Overlapping trips give an infinite
   implied speed; this was a 500 on any bill with a double-booked truck.
4. **An unreadable trace read as proof of fraud.** A trace that failed to
   download produced no points, which looked identical to a truck that never
   reached the dump site, turning an S3 problem into a hard R5 hold.

## Two decisions worth knowing

- **R1's soft band is now in the plan** (§5): inside 30 m passes, 30–60 m is
  soft, beyond 60 m is hard. Without it drain 8 reads red and the demo loses its
  "not every flag is fraud" beat.
- **Approving a drain releases everything the rules withheld on it**; holding
  moves its review tonnage to held. That is what makes the plan's headline come
  out exactly: 805 t verified with 65 t in review, and approving the two amber
  drains on camera gives **870 t verified, ₹6.66 lakh held**.

## What is left for live AWS

Nothing in the list below is a code change — it is all first contact.

1. **Account activation.** Still the blocker.
2. **Deploy** (section 3 above). Then `curl $API_URL/health`, which now reports
   both model ids and whether mock mode is on.
3. **Seed**, then `POST /verify` for real. The things that meet reality for the
   first time: Textract's QUERIES block relationships, the Bedrock Converse
   `toolUse` block, `geo-routes` leg geometry, and DynamoDB accepting trip items
   that carry the full findings list.
4. **Amazon Location API key** — the map has still never rendered against a real
   style.
5. **Live upload** — wire a button to `POST /upload-url`, which has never been
   run against real S3.
6. **Re-measure the pHash threshold** once the real photos exist; 12 was
   measured on the generated set.

## Commands added since Day 1

```bash
python scripts/make_demo_fixtures.py   # offline API snapshot for the frontend
cd frontend && npm test                # component tests
```
