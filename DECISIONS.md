# Decisions taken unattended (Day 1 afternoon/evening session)

Written while the account was still pending activation, so nothing here was
verified against live AWS. Every item is a choice I made rather than something
the plan dictated.

## Reconciling the plan's demo totals (plan §6)

The plan asks for "Claimed 1,240 t · Verified 870 t · Hold ₹6.66 lakh" **and**
for amber review drains (6, 8). Those are mutually inconsistent: ₹6.66 lakh of
hold is exactly 370 t at ₹1,800/t, and 870 + 370 = 1,240 leaves nothing in
review.

**Chosen reading:** the plan's three numbers describe the state *after* the
engineer approves the review drains, which is what the demo does on camera.
So the seeded dataset is:

| | tonnes | ₹ |
|---|---|---|
| Claimed | 1,240 | 22.32 lakh |
| Verified (before decisions) | 805 | 14.49 lakh |
| Review (pending) | 65 | 1.17 lakh |
| **Hold** | **370** | **6.66 lakh** |

Approving the two review drains moves 65 t into verified → **870 t verified,
₹6.66 lakh held**, exactly the plan's headline. Both numbers in the §10 video
script (claimed 1,240 t, hold ₹6.66 lakh) are exact at all times.

## Drain 8 reads amber, so R1 needs a soft band

Plan §6 says drain 8 (a photo outside the geofence) is 🟡, but §5 lists R1 as a
**hard** rule, which would make it 🔴. Resolution: R1 gets a soft band — a photo
between the 30 m buffer and 60 m from the drain is a **soft** fail (consumer
GPS is routinely off by tens of metres), beyond 60 m is a hard fail.
`ground_truth.json` records drain 8's trip as `REVIEW` with `softFails: ["R1"]`.
**Day 2 must implement that band** or drain 8 will come out red.

## Drain 11 is 🔴, not 🟡

Plan §6 writes "🟡/🔴" for drain 11. R7 (net weight over vehicle capacity) is a
hard rule in §5, so the trips with 14 t on a 10 t truck are `HOLD` and the drain
is red.

## Two Bedrock model parameters

`BedrockModelId` stays for the Day 2 evidence summary. Photo vision gets its own `BedrockVisionModelId`, defaulting
to `in.anthropic.claude-haiku-4-5-20251001-v1:0` as instructed — ~40 photo calls
per seed is the main recurring cost, and Haiku is the cheapest entitled model in
ap-south-1. Both are stack parameters and plain env vars, so either can change
without a code edit.

Both now default to Haiku 4.5. `check_region.py` passed it in ap-south-1 on
2026-10-08, while `in.anthropic.claude-sonnet-5` came back "not available for
this account" and the old Opus default was never verified.

## Ingest concurrency is not reserved by default

Plan and CLAUDE.md ask for reserved concurrency 3 on the ingest function. A new
account's Lambda concurrency quota is 10, and Lambda refuses any reservation
that leaves fewer than 10 unreserved, so the first `sam deploy` rolled back.
`IngestReservedConcurrency` (default 0 = unreserved) makes it a parameter; at a
quota of 10 the account itself caps ingest below the throttling risk. Once the
quota is raised, deploy with `IngestReservedConcurrency=3`.

Sampling is fixed at `temperature` 0 and `topP` 1.0 on both Bedrock calls, so
the same photo gets the same verdict every run (unset, Nova Pro called one
photo `unclear` at 0.0 and then `silt` at 0.8). Anthropic model ids get
`maxTokens` only: the Claude 5 family returns a 400 on `temperature`, and
Claude 4.5 refuses `temperature` and `topP` together.
`bedrock.inference_config()` holds that rule.

## Bedrock vision uses Converse + forced tool use

`converse` with a `toolConfig` whose single tool pins the output schema
(`cleared`, `load_type`, `confidence`, `notes`) and `toolChoice` forcing that
tool. This is schema-guaranteed output instead of parsing prose. Forced tool
choice is fine on Haiku 4.5. The IAM action for Converse is still
`bedrock:InvokeModel`, which the template already grants.

## One backend package, both Lambdas

`CodeUri` for both functions is now `../backend/` with handlers
`ingest.app.lambda_handler` and `api.app.lambda_handler`, so they share
`backend/common/` without a Lambda layer or duplicated files. Dependencies live
in one `backend/requirements.txt`. Simplest thing that keeps one copy of the
provider layer.

## Mock mode is manifest-driven

`MOCK_AWS=1` makes the providers return canned **API-shaped** responses, which
the real parsers then parse — the same parsing code path either way, as asked.
On top of the static fixtures, `MOCK_MANIFEST_PATH` can point at
`data/out/mock_manifest.json` (written by the generators), so a mocked Textract
call returns the values that were actually printed on that slip image. That is
what makes the offline end-to-end test meaningful rather than circular.

## Metric buffering without pyproj

`osm_drains.py` buffers drain centrelines by 30 m using shapely in a local
equirectangular projection centred on the ward (metres via
`111_320 m/deg` latitude and `111_320·cos(lat)` longitude). Over a single ward
(a few km) the error is centimetres, and it avoids adding pyproj to the repo.

## Generator order: trips before slips

A slip has to agree with its trip (vehicle number, net weight, time in), so
`gen_trips.py` writes `trips.json` first and `gen_slips.py` renders images from
it. Running `gen_slips.py` without trips is an explicit error, not a silent
fallback.

## Dump site is derived, not hard-coded

No city is hard-coded anywhere. `--center` (or the ward centre from
`drains.geojson`) drives everything, and the dump site defaults to a polygon
8 km north-east of the centre, overridable with `--dumpsite lat,lon`. Replace it
with the real landfill in the morning.

## Photo placeholders

`data/gen_photos.py` (a new file, not in the original list) writes test JPEGs
with real EXIF GPS + timestamps via piexif, including the exact duplicate and
the cropped/brightened duplicate that plan §6 asks for. `seed.py --photos-dir`
plus `--photo-map` takes over once the real shoot exists; the mapping format is
documented in `data/PHOTO_MAPPING.md`.

## Safety defaults

Everything that could spend money defaults to not spending it: `seed.py`,
`reset.py` and `gen_trips.py` are dry-run/offline unless `--live` is passed, and
`--live` first prints the exact call plan and a billable-call estimate. No
command in this session touched the AWS account.

---

# Second batch (Day 1 afternoon/evening build)

## A pHash index item, alongside the evidence item

Rule R3 has to compare a photo against every other photo in the bill. Evidence
items are keyed `EVID#<s3key>`, one partition each, so answering that would
mean scanning the table. The ingest Lambda therefore writes a second small item
per photo: `BILL#B1 / PHASH#<hash>#<key>`. R3 becomes one `begins_with` query.
It is not in the plan's section 4 table, but it is the same data, and the drain
query already filters by `DRAIN#` so nothing else sees it.

## Duplicate threshold is 12 bits

Measured on the generated set: an exact copy is 0, the cropped-and-brightened
copy is 6, and the closest unrelated pair is 16. `PHASH_DUPLICATE_MAX = 12` sits
in the gap. **Re-measure after the real photo shoot** - real photos of the same
drain from slightly different angles may land closer together than these do.

## One slip per trip, not forty

Plan section 6 says "about 40 slips", but rules R7, R8 and R9 are per trip, and
with 117 trips a 40-slip dataset leaves two thirds of the bill unverifiable.
The generator renders one slip per trip and `seed.py --max-slips N` caps what
gets uploaded, so a first live run can be cheap. At full size a seed is 117
Textract pages (~$1.76) and 40 Bedrock photo calls (~$0.16).

## Heavy dependencies in a Lambda layer

`imagehash.phash` needs scipy's DCT, which brings numpy: ~204 MB with Pillow.
Carrying that in both functions put each at 82% of the 250 MB unzipped limit
and slowed the api function's cold start for no reason. The layer is attached
to ingest only, and both function artifacts are now 156 KB, so Day 2 code
pushes are seconds rather than minutes.

## Trips are 117, not 120

"About 120" in the plan. 117 is what falls out of hitting every tonnage target
exactly with believable 5-15 t loads. Forcing it to 120 would mean fudging a
load somewhere.

## Drain 14 claims 192 t

The hero drain has to carry enough tonnage that holding all of it, plus the
other three red drains, comes to exactly 370 t. 192 t is 2.8x the average
drain. That reads as a feature rather than a bug - the biggest claim on the
bill is the one that never happened - but it is a choice, not the plan's.

## Bugs the tests caught

Worth knowing about, since each was silent:

1. `parse_weight_tonnes("not a weight")` returned **0.0 t**: the OCR corrector
   turned the letter o into a zero. Digit correction now requires the text to
   contain a real digit first.
2. `parse_vehicle_no("MH O1 AB l234")` returned `L234`: upper-casing ran before
   the digit fix, and `L` was not in the correction table.
3. `split_long_lines` gave its last section every leftover point, making it
   several times longer than the others. Sections are now split on evenly
   divided indices.
4. A pHash test written with flat two-tone images passed nothing: such images
   have almost no low-frequency content, so they hash within a few bits of each
   other however different they look. The same effect made the *first* version
   of the photo generator produce 40 photos that were all mutual duplicates.

## Still mocked, not proven

Nothing in this session called AWS. The response shapes in
`backend/common/fixtures/` are written from the documented API shapes, so the
parsers are tested against what the services *should* return. The three that
matter on first contact: Textract QUERIES block relationships, the Bedrock
Converse `toolUse` block, and `geo-routes` leg geometry. The Location parser is
written defensively (it searches for the geometry rather than assuming a path)
because that shape is the least certain of the three.

---

# Third batch (Day 2: rules, API, decision screen)

Written on 8 October with the AWS account still blocked and a support case
open. Nothing in this batch called AWS either.

## Photo rules taint the whole drain, trip rules taint one trip

R1-R4 and R10 judge a drain's own evidence, so a failure applies to every trip
billed against that drain. A reused after-photo does not discredit one lorry
load, it discredits the claim the photo was submitted to support. R5-R9 and the
GPS-gap flag judge a single trip.

This is what `ground_truth.json` already assumed — drain 3's twelve trips all
carry `R4`, drain 14's eighteen all carry `R3` — so the alternative would have
contradicted the dataset.

## R4 is scoped by photo role

Every before/after photo comes back from the vision model as
`load_type: unclear`, because there is no load in a picture of a channel.
Applying the load check to all photos would have put every drain in review. So:

* **load** photos: `debris` is a hard fail, `unclear` is soft, `silt` passes.
* **after** photos: `cleared: false` is a **soft** fail. The plan only makes
  debris hard, and one frame's judgement about whether a channel looks clear is
  weaker evidence than a tipper visibly full of broken brick.
* **before** photos: not checked for clearance — a before photo is *supposed*
  to show silt.

## R3 needs an order, and time is the only honest one

Two identical photos on one bill prove a reuse, but not which copy is the
original. The rule takes the **earliest** appearance of an image as genuine and
flags every later one.

That exposed a generator bug: the reused drain 14 photos were stamped four days
*before* the drain 9 photo they copy, so the rule would have cleared drain 14
and flagged drain 9. The generator now stamps the copies a day after their
source. If the real shoot produces a reuse, its timestamps have to run the same
way round.

## Missing evidence is review, never hold, never silence

A trip with no slip, no readable trace, or an ingestion error gets its own soft
finding (`SLIP_MISSING`, `TRACE_MISSING`, `PHOTOS_MISSING`, `EVIDENCE_ERROR`)
and goes to the engineer. Two reasons: absence of evidence is not evidence of
fraud, and a hard fail here would mean an S3 permissions problem silently
reading as "the contractor stole the money".

The same thinking fixed a real bug. R5 asks whether the trace enters the dump
site; a trace that could not be *downloaded* produced no points, which looked
exactly like a truck that never arrived. Now no readable trace means no R5
verdict at all, just a soft flag saying the route could not be checked.

## What a decision does to the money

* **APPROVE** releases everything the rules withheld on that drain — review and
  held alike. An engineer who has been to the site can overrule the evidence,
  which is the whole point of showing them the evidence.
* **HOLD** moves the pending review tonnage across to held.
* No decision leaves the evidence to speak for itself.

This gives the plan's headline exactly: the bill verifies at 805 t with 65 t in
review and 370 t held, and approving the two amber drains on camera takes
verified to **870 t** with the hold still at **₹6.66 lakh**.

## Verification reads traces from S3, not from DynamoDB

R5, R6 and R8 need the actual GPS points, and only a summary of each trace is
in DynamoDB. The API fetches the trace objects from S3 in parallel (16 threads)
during verification: 117 small JSON files, well inside the HTTP API's 30 s
ceiling, and measured at well under a second against moto.

The alternative — having ingestion precompute dump-site entry and arrival —
would be faster still but bakes the dump site into the extraction, so moving
the dump site would mean re-ingesting every trace. Not worth it for a ward.

## One filtered scan for the photos

Evidence items live one per S3 key (`EVID#<key>`), so there is no partition to
query for "every photo on this bill". The API uses a filtered `scan` on
`sk = PHOTO`. The table holds a few hundred items for one bill, so this is the
honest MVP answer; a sparse GSI on `billId` is the production one. Slips,
traces and vehicles are fetched by known key with `BatchGetItem` instead.

## Verdicts are persisted, not recomputed

`POST /verify` writes each drain's verified/review/held tonnage onto the drain
item, so `POST /decision` can move money with one update and a re-summarise
rather than re-running every rule. `GET /bill` reads stored state and reports
`PENDING` with nothing verified until a verification has run.

## The frontend has an offline snapshot

`scripts/make_demo_fixtures.py` drives the whole pipeline through moto and
saves what the API returned into `frontend/public/data/demo/` (≈620 KB, 19
files, committed). With `VITE_API_BASE_URL` empty the app runs off that.

It exists because the account is blocked and there was otherwise no way to look
at the screen at all; it doubles as a fallback if the stack is unreachable while
recording. Decisions are applied client-side in that mode with the same
arithmetic as the backend, so the approve-and-release moment still works.
Regenerate it any time; it is generated, not authored.

## Fleet sizing

Ten trucks could not cover 18 drains working in parallel without double-booking,
so the fleet is generated (22 by default) and `gen_trips.py` hires another
rather than put one truck on two simultaneous trips. `seed.py` writes capacity
records for the trucks that actually ran, so R7 always has a capacity to
compare against.

## Still not proven against live AWS

Everything in this batch was tested against moto and `MOCK_AWS=1`. What meets
reality first: the Textract and Bedrock response shapes (unchanged from Day 1),
the presigned-PUT flow in `POST /upload-url`, DynamoDB's behaviour on items
carrying the full `ruleDetail` findings list, and the Amazon Location map style,
which still needs the API key.

---

# Fourth batch (offline map)

The dashboard was unusable without AWS: the map panel was blank with a toast
asking for Amazon Location configuration that cannot exist until the account
is activated. Fixing that did not change the live architecture — Amazon
Location is untouched and is still what ships.

## The map was blank for a second, worse reason

Before any of the fallback work, the map rendered **nothing in any mode**.
The bundle asked for `maplibre-gl-worker.mjs`; the Vite build never emitted
one; MapLibre reported "Worker failed to load". An Amazon Location key would
not have helped.

`src/maplibre-worker.ts` imports the worker with Vite's `?url` and hands it to
`setWorkerUrl`, so it is emitted as a hashed asset on our own origin. Found by
screenshotting the running app — it was the first thing the screenshot showed,
and no unit test would have caught it.

## Two modes, one data layer

`initialMode()` picks Amazon Location when both `VITE_AWS_REGION` and
`VITE_LOCATION_API_KEY` are set, and the bundled style otherwise. The map also
swaps on a style error, or if the style has not loaded after six seconds.

The important part is that **the drains do not belong to either style**. They
are added once the style is ready in whichever mode, and added again on
`styledata`, because `setStyle` discards every source and layer. Screenshot 4
is that path: the app starts in location mode against a URL that 404s, swaps,
and comes back with all 18 drains coloured.

Interaction handlers are registered once, outside that function. Inside it,
every swap would leave another copy and a click would open the drain twice.

## The offline style contains no URL at all

No `glyphs`, no `sprite`, no tiles, no symbol layer — and the backdrop GeoJSON
is **inlined into the style object** rather than referenced by URL. The app
fetches the file itself (same origin, shipped with the bundle) and passes the
parsed object in. `styleIsSelfContained()` asserts no `http(s)` URL appears
anywhere in the style, and a test holds that.

## The basemap is real OSM, fetched at generation time

`data/osm_basemap.py` queries Overpass for the ward's roads and water and
writes one simplified file. Two tiers: all streets around the drains, main
roads and water across the wider box out to the dump site — otherwise the
corridor the hero case drives along is empty. 2,364 roads and 51 water
features, 416 KB, 46 KB gzipped, committed.

Overpass answers 504 about as often as it answers, so the script tries several
mirrors and, failing all of them, writes a synthetic street grid flagged as
such in the file. **If that grid ever ends up in the video, say so** — it is
not the real ward. A missing file is not an error either: the style draws
plain paper.

## The demo starts unverified

The offline snapshot is already verified, so `getBill` serves it through a
pending projection — grey drains, claim only — until Run Verification is
pressed, which replays it verified with the figures counting up. Live, the API
already did this. `?state=verified` and `?drain=14` skip ahead while working
on the screen; `?style=<url>` overrides the basemap, which is how the
failure path is exercised without a key.

## Screenshots instead of a WebGL test

jsdom has no WebGL, so component tests stub the map. What the map actually
draws is checked by `npm run screenshot`, which drives the already-installed
Chrome through `puppeteer-core` (nothing downloaded) with SwiftShader for
software WebGL, and saves four views under `frontend/screenshots/`. They are
committed at 1x: four retina PNGs came to 3 MB.
