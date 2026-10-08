# SiltProof: three design directions

Design only. Nothing here touches `frontend/src`, the backend, the API or the
totals. Each prototype is one self-contained HTML page that reads the real API
snapshot (`shared/data/`, copied from `frontend/public/data/`) and derives
every position, label and camera from it, so a new 18-drain ward drops in by
replacing those files. MapLibre and Google Fonts come from CDNs inside the
prototypes only.

## View them

```sh
cd design
python3 -m http.server 8765
```

| | Pre-verification | Verified | Drain 14 | Components and states |
|---|---|---|---|---|
| A, Survey Sheet | http://localhost:8765/direction-1/ | [?view=verified](http://localhost:8765/direction-1/?view=verified) | [?view=drain14](http://localhost:8765/direction-1/?view=drain14) | [?view=system](http://localhost:8765/direction-1/?view=system) |
| B, Night Twin | http://localhost:8765/direction-2/ | [?view=verified](http://localhost:8765/direction-2/?view=verified) | [?view=drain14](http://localhost:8765/direction-2/?view=drain14) | [?view=system](http://localhost:8765/direction-2/?view=system) |
| C, The Docket | http://localhost:8765/direction-3/ | [?view=verified](http://localhost:8765/direction-3/?view=verified) | [?view=drain14](http://localhost:8765/direction-3/?view=drain14) | [?view=system](http://localhost:8765/direction-3/?view=system) |

All three are live. Press the verify button on the first view, then click
drain 14 on the map or in the list. Extra parameters: `&play=scan` or
`?view=verified&play=open14` autoplay the motion, `&hover=3` shows a hover
state, `&confirm=hold` shows the confirmation and `&decided=1` shows the
signed decision. Only drain 14 carries full evidence; other drains say so.

Screenshots, close-ups and videos are in `screenshots/`, all at 1920×1080 with
device scale factor 1. To regenerate them (Playwright from a scratch install,
plus `npx playwright install ffmpeg` for video):

```sh
node capture.mjs all all
```

| File | What it shows |
|---|---|
| `N-1-pre-verification.png` | bill unchecked |
| `N-2-verified.png` | after verification, ₹6.66 lakh held |
| `N-3-drain-14.png` | the drain 14 investigation |
| `N-4-hover.png`, `N-5-confirm-hold.png`, `N-6-held.png` | hover, confirmation, decided |
| `N-7-components.png` | colour, type, buttons in every state, loading, errors |
| `N-closeup-evidence.png` | slip and photos at the size they appear in the video |
| `N-scan.webm`, `N-open-drain-14.webm` | the motion, 4 to 8 s each, recorded from the first drawn frame |

## What the current UI gets wrong

The current UI does everything the demo needs: verify, colour the drains, drill
in, explain, approve or hold. The weaknesses are all presentation:

- **The money has no hierarchy.** Claimed, verified, review and hold sit in
  one equal-weight KPI row in a dark header. The ₹6.66 lakh is no larger than
  the 805 t beside it.
- **The map wastes its frame.** It fits the dump site 8 km away, so the 18
  drains are small marks in the bottom-left quarter.
- **The evidence is text only.** No photo or slip image is shown. The plan's
  "photo from another drain" and "slip printed early" are sentences, not
  something you can see.
- **There is no time.** R8 is a timing argument (slip at 06:53, truck leaves at
  07:12), but there is no timeline to show it.
- **The drain panel repeats itself.** R3 is listed twice, 18 identical trip
  chips sit above the slip table, and the decision buttons are below the fold
  at 1440×900.
- **The claimed route is a dashed line with no label,** so a viewer cannot tell
  which line is the bill and which is the GPS.

## The three directions

**A, Survey Sheet** (Geospatial Intelligence Studio). The ward is drawn as a
surveyed sheet: a neatline, a lat/long graticule, a north arrow, and a title
block that holds the legend and scale. The engineer's working paper lies over it
on the left, with the held amount set large in a madder-red serif. Tonnage is
shown as ring symbols: a ring's area is tonnes billed, split into verified,
review and held. There is no height on the map at all. The verification is a
survey sweep down the sheet, and the held figure accumulates drain by drain as
the line passes. Drain 14 opens into three columns: the case and decision on
the left, the corridor map with a two-lane timeline (what the slip says, what
the GPS says) in the middle, and the evidence on the right.

**B, Night Twin** (Cinematic Digital Twin). The ward at night, tilted 56°, with
light meaning evidence: a drain glows once it has been checked. Each drain has
a stacked data column (verified, review, held) that stands 110 m east of the
drain, with a ground label "14 | 192 t claimed, 0 t verified" and a legend
saying "height is tonnes billed, 1 t to 3 m; not the drain's depth or the
silt's volume". The verification is a radial scan ring while the camera
drifts. Drain 14 flies along the haul corridor, replays all 18 trucks
together, stops every one of them 2.2 km short of an extruded dump-site fence,
and drops a pulsing beacon. A scrub bar shows the slip clock against the truck
clock.

**C, The Docket** (my original concept). The contractor's bill is the
interface, because the bill is what the engineer is signing. Each of the 18
line items carries a trip strip, the claimed tonnes and rupees, an auditor's
mark and the payable amount. Only exceptions are coloured: a clean line gets a
plain ink tick, a held line a red stamp with the rupees, a review line a dashed
amber stamp. The engineer's own marks are ballpoint blue. The verification is
an audit line that walks down the bill, stamping as it goes, while the map plate
follows the current line and the footer totals add up. Drain 14 compresses the
bill into a column and lays out Exhibit A (GPS map), B (slip) and C (photo
hash), with a signature block.

### How each handles the 3D height rule

| | Tonnage shown as | Why it cannot read as drain volume |
|---|---|---|
| A | ring area, flat | no height anywhere; legend says "Ring area is tonnes billed" |
| B | stacked data columns | offset from the drain, labelled in tonnes, legend states the scale and says it is not depth or volume; columns hidden in the case view |
| C | numbers in a table, trip strips | no map encoding of tonnes at all |

## Comparison

| | A, Survey Sheet | B, Night Twin | C, The Docket |
|---|---|---|---|
| **Visual impact on video** | High, calm and expensive-looking | Highest, the truck replay is the most memorable shot | High, and the most unusual |
| **Map as the centre** | Yes, framed and central | Yes, full bleed | Partly: the bill leads, the map is a plate |
| **Financial hierarchy** | Strong, 80 px held figure plus a ledger bar | Strong, 128 px figure, but it competes with the 3D | Strongest: the bill's own total, stamped amounts per line |
| **Readability at 1080p** | Very good | Good. Dark UI and thin type need care; labels collide where drains cluster | Very good, but a dense table |
| **Usability for the engineer** | Clear: register, map, case file | Weakest: the 3D is harder to read and click | Best fit to the task: line by line, like a real bill |
| **Technical risk** | Low | High: camera framing per ward, extrusion performance, flyTo timing on the recording machine | Low |
| **Amazon Location fit** | Monochrome Light style, recoloured | Standard or Monochrome Dark | Monochrome Light |
| **Build time in our app** | ~16–20 h | ~28–34 h | ~14–18 h |

Estimates assume the existing React + MapLibre app, the same API, no new
dependencies (everything above is MapLibre layers, HTML markers and CSS), and
updating the existing vitest tests.

### What to cut with only 4 hours

- **A:** keep the palette, type, left sheet, ring symbols and three-column
  drain 14 layout. Cut the graticule ticks, the survey sweep (count up the
  figures instead), the Bedrock photo strip and the two-lane timeline (use one
  line).
- **B:** keep the dark style, the pitch and the data columns with their legend.
  Cut the scan ring, the camera flight and the 18-truck replay (draw one route
  progressively). Without its motion, B loses most of its point.
- **C:** keep the bill table, stamps, totals and the exhibit layout, reusing the
  current MapView as Exhibit A. Cut the audit-line animation (stamp all lines at
  once with a short stagger) and the map plate's hover sync.

## Recommendation: A, Survey Sheet

It best meets the brief's core requirement, which is to make maps and evidence
the product, while still giving the money a clear first place on screen. It
reads well on a recording and carries the least risk for a hackathon, with no
3D framing to tune per ward. When the real ward arrives tomorrow, the survey
treatment (neatline, graticule, ring symbols) still looks deliberate on any
geometry.

Two borrowings worth considering, each about 2 h: C's red stamps on A's drain
register, and B's 18-truck replay drawn flat in A's palette on drain 14. Both
are optional; A stands without them.

If the video matters more than the engineer's screen, choose B. It has the
strongest single moment, but it is the slowest to build and the riskiest to
record.

## Found while doing this (not design, but blocking)

1. **The slip image on disk is stale.** `data/out/evidence/slips/B1/14-001.png`
   shows `MH 04 NP 6789`, net 7.40 t, time in 06:43. `trips.json` and the API
   snapshot say `MH 03 AL 2370` and 06:53. Showing that image beside the
   extracted fields on camera would contradict them. The prototypes render the
   slip from the snapshot's fields instead. Regenerate the slips before
   recording.
2. **The snapshot's evidence summary says 40 minutes; R8 says 44.**
   `drain-14.json` has `summaryModelId: null` (a canned summary) and its photo
   `modelId`s are still Haiku, not Nova Pro, so the snapshot predates the model
   switch. `python scripts/make_demo_fixtures.py` should refresh it. The
   prototypes say "Amazon Bedrock" without naming a model.
3. **Showing photos needs image URLs.** `GET /drain/{id}` returns photo
   `s3Key`s only. Every direction puts the photos on screen. That needs either
   presigned GET URLs added to the response (an API change, so your call) or,
   offline only, the images copied into `public/data/demo`.
4. **The photos are placeholders** (synthetic shapes labelled "SIMULATED
   EVIDENCE"). Every direction shows them true-colour with a "Simulated photo"
   tag and a persistent "Simulated data" badge.

## Notes

- `shared/sp.js` holds the shared data loading, geometry and formatting.
  `shared/data/findings-by-drain.json` is derived from the 18 `drain-N.json`
  fixtures, so each drain's one-line reason comes from its own findings.
- The UI/UX Pro Max database proposed an OLED-dark, Fira, green-accent system.
  I used its checklist (contrast, focus rings, reduced motion, 44 px targets,
  loading feedback) and not its palette, which is the generic ops-dashboard
  default.
- Every prototype respects `prefers-reduced-motion`; the motion collapses to the
  final state.
