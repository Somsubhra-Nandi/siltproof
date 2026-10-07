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
