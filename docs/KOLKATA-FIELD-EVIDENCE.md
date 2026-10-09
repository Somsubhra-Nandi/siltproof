# Kolkata field evidence (9 Oct 2026)

A separate, real field-evidence case. It does **not** replace or relocate the
18-drain simulated investigation (Bill B1), which stays exactly as it is.

This file is the public summary. The original photographs, precise GPS
coordinates, device metadata and raw OSM/OSRM responses are kept in a private
archive outside this repository and must not be committed.

## What was captured

- **11 original photographs**, taken with one Android phone on
  **2026-10-09 between 09:53 and 09:59 IST**, in about five minutes.
- Originals transferred with EXIF intact (not via WhatsApp).
- Located on the south-eastern fringe of New Town, Kolkata.

## What the photographs show

All 11 frames appear to show **one continuous channel**:

- unlined, earth-banked open channel, a few metres wide;
- banks and bed heavily overgrown; floating and emergent vegetation;
- stagnant, turbid grey water, with some floating plastic and litter;
- a concrete culvert or parapet edge in the frames at one position, where an
  OSM-mapped local road crosses the walk.

No frame shows a silt heap, a truck, a load, rubble, or a cleared bed. The
photographs are **current-condition evidence only**. They do not prove that
any desilting happened, before or after, and they must not be labelled as
before/after cleaning photos.

## GPS

- **5 distinct GPS fixes** across the 11 photos (2-3 photos share each fix).
- Fixes span about **82 m in a straight line** (about 110 m along the walk),
  consistent with one person walking along a single channel.
- **Five fixes are not five drains.** Distinct fixes are positions along one
  channel, not separate drain sections.
- Accuracy is **unknown**:
  - no GPS accuracy tag;
  - GPS altitude is recorded as 0 on every photo;
  - consecutive photos 30-60 s apart repeat identical coordinates, which
    suggests cached or network-assisted fixes.
- Treat each fix as uncertain by tens of metres.

## OpenStreetMap coverage

Checked with public Overpass queries on 9 Oct 2026.

- **The photographed channel is not mapped in OSM.**
- No `waterway=drain` or `waterway=ditch` exists within about 1.6 km.
- The nearest mapped linear waterway is a short, isolated unnamed stream
  roughly 200 m away. It connects to nothing in OSM.
- The nearest connected network (Bagjola Canal and its feeder streams) is
  roughly 600 m away. The named canals (Kestopur/Keshtopur, Krishnapur) are
  1.3-1.5 km away.
- Whether the photographed channel physically connects to any of these is
  **unknown**. No connectivity should be drawn to fill that gap.

Any geometry for this channel would have to be **field-approximated** from
the photo fixes. That makes a GPS-in-geofence check partly circular, so it
must be labelled approximate.

## Administrative ownership

**Not established.** OSM's administrative boundaries place the site in
Bhangar-II block, South 24 Parganas, inside the Kolkata Metropolitan Area,
with no municipal-level boundary around it. That may or may not match on-the-
ground responsibility. The case must not name NKDA, KMC, a panchayat or any
contractor as responsible for this channel.

## Dhapa

Dhapa Landfill is a real, mapped landfill in eastern Kolkata, about 14 km by
road from the site (OSRM on OSM data, gate not verified). It is **not** an
approved destination for this field case, and nothing here should imply that
it is.

## Why Bill B1 cannot take these photos

- B1's work window is **21 Sep - 4 Oct 2026**. The photos are dated
  **9 Oct 2026**, so R2 would hard-fail every one and hold whatever drain
  they were attached to.
- B1's drains are synthetic Mumbai geometry, so R1 would fail by about
  1,700 km.
- Used as `after` photos, the vision model would very likely report the
  channel as not cleared, a soft R4 fail.

Moving B1's window or geometry to fit would change the seeded totals
(₹6.66 lakh held). B1 is therefore left unchanged.

## Image size and metadata

- Originals are **2.9-7.8 MB**, 4624 x 2080 px (two portrait frames are
  2080 x 4624 with EXIF Orientation 0, not 1).
- `backend/ingest` sends the raw bytes to Bedrock with no resize, under its
  own 8 MB cap. Whether Nova Pro accepts images that large through Converse
  is **untested**. The live fixtures used small generated JPEGs.
- EXIF `DateTimeOriginal` has **no timezone offset**
  (`OffsetTimeOriginal` absent). Times are IST only because the phone was set
  to IST. The rules currently assume the work window's timezone.
- The EXIF camera-model string carries embedded NUL padding. Strip it before
  storing or displaying it.
- pHash (`backend/common/photo.py`):
  - distinct real photos are 22-38 bits apart, against the R3 duplicate
    threshold of 12, so there are no false duplicates;
  - a downscale to 1568 px leaves the hash unchanged;
  - the nearest synthetic B1 image is 14 bits away.
- Blur any faces or number plates before a public demo.

## Approach for the independent 9 Oct field trial

A separate trial, processed live through AWS, alongside B1:

1. Its own trial ID and evidence namespace. Nothing is written under B1.
2. Trial date = 9 Oct 2026, the real capture date.
3. Upload the originals; extract EXIF and pHash from the original bytes.
4. Run Nova Pro on each photo and show the response as returned.
5. Compare GPS against a clearly labelled, approximate field geometry.
6. Report current condition. Where the evidence cannot decide something, the
   result says so.

## Requirements for the judge-upload API (teammate's contract)

This repo does **not** implement a second upload API. When the teammate's
trial API contract arrives, it should meet these requirements:

- **Separate namespace.** Trial evidence lives under its own ID and key
  prefix (not `photos/B1/...`), and its records never mix with Bill B1.
- **Trial date.** The trial's date or window matches the photographs' real
  capture date (9 Oct 2026), not B1's window.
- **EXIF first.** GPS, timestamp and pHash are extracted from the original
  upload before any resize, re-encode or rotation, because transformations
  can strip EXIF.
- **Traceable AI copy.** Any resized copy sent to Nova Pro records:
  - the original's S3 key;
  - the SHA-256 of both files;
  - both pixel sizes.
  The original is preserved unmodified.
- **Approximate geometry.** The GPS check runs against field geometry
  labelled "approximate, field-derived, not an official drain map". It
  reports distances, not proof.
- **Unknown is an answer.** Missing EXIF, no GPS, unknown accuracy,
  `unclear` vision output or no reference geometry each give an explicit
  unknown or insufficient-evidence result, never a silent pass or a fraud
  verdict.
- **Nova Pro verbatim.** The model's actual response (fields, confidence,
  notes, model ID) is shown without editing or re-wording, and is visually
  distinct from rule findings.
- **B1 untouched.** No changes to Bill B1's data, totals, ground truth, or
  the semantics of rules R1-R10. Any trial-specific checks live alongside
  them, not inside them.
- **No assumptions.** Nothing assumes the channel is officially mapped, has a
  known owner, or that Dhapa is an approved destination.
