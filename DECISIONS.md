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

`BedrockModelId` (default `in.anthropic.claude-opus-5`) stays for the Day 2
evidence summary. Photo vision gets its own `BedrockVisionModelId`, defaulting
to `in.anthropic.claude-haiku-4-5-20251001-v1:0` as instructed — ~40 photo calls
per seed is the main recurring cost, and Haiku is the cheapest entitled model in
ap-south-1. Both are stack parameters and plain env vars, so either can change
without a code edit.

`temperature` is deliberately **not** sent: it is removed on the Claude 5 family
and returns a 400 there, and omitting it works on every entitled model.

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
