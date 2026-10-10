# SiltProof design: the approved hybrid

Direction A's survey sheet is the visual language, Direction C's case file is
the drain investigation, and Direction B contributes one camera move. The
prototype is `design/hybrid/index.html`; its captures (`node capture-hybrid.mjs`) go to
`screenshots/hybrid/` and `videos/hybrid/`, which are not committed. Decisions taken overnight
without you are in `DECISIONS-hybrid.md`; the build plan is in
`IMPLEMENTATION-PLAN.md`.

## Principles

1. **The money first, then the place, then the proof.** The overview answers
   "how much is held, and where" (₹6.66 lakh, four red rings). The case file
   answers "why": three numbered exhibits, each one rule's evidence.
2. **One loud thing per screen.** On the overview, the held figure and its
   stamp. On the case file, ₹3,45,600 and the stopped red trace. Everything
   else is ink on paper.
3. **Every mark is data.** Ring area is tonnes billed. A stamp means a hard
   rule failed (HELD) or a soft one did (REVIEW, dashed). A rule chip names
   the rule. Nothing has height, so nothing can read as drain volume.
4. **Rule findings and AI observations never look alike.** A finding is a red
   bordered box naming the rule. An AI observation is a dashed grey box in
   italic, captioned "AI observation, not a finding", with its provenance.
5. **Simulated means labelled.** A disclosure on the first screen, a
   "Simulated data" badge throughout, "Simulated photo" and "Sample slip" on
   every image, and "Offline summary, no model was called" wherever offline
   text stands in for Bedrock.

## Type

| Role | Face | Size and weight |
|---|---|---|
| Held figure (overview) | Newsreader | 84 px, 500, opsz 72, -0.035em |
| Case amount | Newsreader | 60 px, 500 |
| Page title (Drain 14) | Newsreader | 44 px, 500 |
| Exhibit titles, confirmations | Newsreader | 22 to 25 px, 500 to 600 |
| Captions and summaries | Newsreader | 16 to 18 px, 400; italic for AI text |
| UI, labels, body | Instrument Sans | 16 px body; **15 px minimum** for anything read in the video; 13 to 14 px only for secondary captions |
| Tabular figures (times, tonnes, rupees in tables, stamps, ticks) | IBM Plex Mono | 15 px, 500, `word-spacing: -0.32em` so "805 t" stays tight |

Inline numbers in running text use Instrument Sans with
`font-variant-numeric: tabular-nums lining-nums`, not the mono. Plex Mono is
new to the app (Google Fonts, or self-hosted in production).

## Palette

| Token | Hex | Use |
|---|---|---|
| `--ground` | `#E3DED2` | Map land, page behind cards |
| `--sheet` | `#F2EFE7` | Working sheet, title blocks |
| `--sheet-2` | `#E9E5DA` | Recessed fills, hover |
| `--paper` | `#FBFAF6` | Exhibit cards, inputs |
| `--rule` | `#CFC9BB` | Hairlines |
| `--ink` | `#1C2A38` | Text, neatline, claimed route |
| `--ink-2` | `#4E5A64` | Secondary text |
| `--ink-3` | `#7D868B` | Tertiary text, borders, not-checked rings |
| `--water` | `#AEC0C3` | Water |
| `--prussian` | `#1F4D78` | **The only action colour**: primary buttons, links, focus, APPROVED |
| `--prussian-hover` / `--press` | `#245A8B` / `#163A5B` | |
| `--verified` | `#39684F` | Verified tonnes |
| `--review` | `#A6731E` | Review fills and stamp border (text: `#8A5F14`, see contrast) |
| `--held` | `#8E2424` | Held money, HELD stamp, GPS trace, Hold button |
| `--held-hover` / `--press` | `#9E2D2B` / `#651616` | |
| `--held-wash` | `#F1DFDA` | R8 note, error |

Contrast (measured):
- Ink on paper is 14.0:1, ink-2 on sheet 6.2:1, held on paper 8.3:1,
  prussian on paper 8.4:1, and text on prussian 7.9:1.
- **Two tokens fail 4.5:1 for small text.** Review amber `#A6731E` is 3.9:1
  and ink-3 `#7D868B` is 3.2:1.
- In production:
  - amber text uses `--review-text: #8A5F14` (5.4:1); the amber fills and
    dashed stamp borders keep `#A6731E`;
  - ink-3 text becomes `#646C71` (4.7:1 on sheet).
- The prototype still uses the lighter values for the review labels,
  confidences and tick labels.

## Spacing and layout

- 4 px base; steps of 8, 12, 16, 20, 24, 32. Sheet gutters are 32 px, card
  padding 16 to 20 px, gaps between cards 14 to 18 px.
- **Overview:** a 500 px sheet on the left, and the map frame filling the rest
  inset by 30 px, with a survey neatline, graticule ticks, a north arrow, a
  title-block legend with scale, and an off-map pointer to the dump site.
- **Case file:** a header (title, held amount with stamp, whole-bill totals).
  Below it, Exhibit A (map plus timeline, about 1,045 px wide) on the left,
  and Exhibits B and C stacked in a 780 px column. Then a 128 px decision bar.
  It is designed at exactly 1920×1080 with no scrolling.
- Corners: 3 to 6 px on controls, 0 on cards and map frames, since they are
  paper.

## Components

- **Buttons:** 48 px minimum height (60 px for Run verification), with a solid
  fill and a 2 px "lip" shadow.
  - Hover lightens the fill.
  - Pressed drops 2 px onto the lip.
  - Focus is a 3 px prussian ring.
  - Busy shows a spinner plus a verb ("Saving decision") on the darker fill.
  - Disabled is flat, grey and `not-allowed`.
  - Hold is the weighted action on a red drain; Approve is the quiet outline.
- **Stamps:** a double border, rotated -3°, with the word in tracked 700 caps
  over the amount in mono.
  - HELD is madder.
  - REVIEW is amber and dashed.
  - APPROVED is prussian and single-ruled.
  - A 320 ms overshoot "slam" plays only when the stamp first appears.
- **Rule chips:** `R5` in a mono outline. They sit at the top right of each
  exhibit.
- **Confirmation sheet:** an inline dialog anchored to the control that opened
  it. It contains:
  - a serif question;
  - one sentence of consequence;
  - a "moves" table showing the money and the bill total after the decision,
    computed with the same `applyDecision` as the backend;
  - the buttons Keep reviewing and Confirm.

  Approving a held drain against its evidence also requires a site note
  (20 characters), and its Approve button stays disabled until then.
- **Decided:** a stamp, the time, the note, and "Change decision".
- **Error:** a madder rule with "Not saved." in bold, what happened, and
  what to do. It sits beside the control, and the bill is unchanged.

## Map styling

- The palette is recoloured from the offline basemap (or Amazon Location
  Monochrome Light live). The map draws no text, so every label is an HTML
  marker in the UI fonts.
- **Rings:**
  - Radius is `6 + 2.1·√t`, so ring **area** is proportional to tonnes.
  - A drain that hasn't been checked has a dashed grey ring.
  - A checked drain's ring is split into verified, review and held arcs.
  - Held drains carry a "Held ₹x.xx L" tag.
- **The case map shows:**
  - the claimed haul as an ink dashed line;
  - GPS trip 001 as a 4.5 px madder line with a paper casing;
  - the other 17 traces as faint madder lines;
  - the dump geofence, hatched;
  - the stop point;
  - a survey **dimension line** with end ticks from the stop to the dump site,
    labelled "2.2 km short";
  - labels for Drain 14, the stop (with both times) and the dump site.

  Other drains drop to 12 % opacity.

## Exhibits

- **A, route.** The map with a two-lane timeline below it.
  - The slip lane shows time in and time out.
  - The GPS lane shows "leaves drain 14", "stops 2.2 km short" and "last fix",
    then "Never arrives at the dump site" in the end gutter.
  - The 15-minute axis is derived from the data.
  - Labels are measured with canvas and placed above or below, left or right
    of their dot, so they never collide whatever the times.
  - Two brackets sit under the lanes: "time-in is 19 min before the truck
    leaves" (dashed), and the R8 bracket, "44 min apart, allowed 10 min".
- **B, slip.**
  - The generated slip image as a slightly rotated scan, with the TIME IN row
    outlined.
  - A 0.6× loupe of the time rows, sharp at 1080p.
  - The Textract fields with their confidences; the disputed one is red.
  - The exact R8 message.
- **C, photos.**
  - A before/after slider over the drain 9 original and the drain 14 copy
    (keyboard: arrow keys).
  - Both 64-bit perceptual hashes drawn as 8×8 grids, with the distance and
    threshold.
  - The R3 finding, then the Bedrock observation.
  - `?photos=slot` shows the reserved, labelled frames for the real
    GPS-tagged photos.

## Motion

Every motion is a reply to a press, except the replay, which is the point of
the shot.

| Moment | Timing | Easing |
|---|---|---|
| Button press | 80 ms transform; 150 ms colour | linear |
| Run verification: busy state | 450 ms "Starting verification" | |
| Sweep | 3,400 ms top to bottom, with figures and ledger updating as each drain is passed | linear (it is a scan) |
| ₹6.66 lakh lands, HELD stamp | 320 ms | `cubic-bezier(.3,1.5,.5,1)` |
| Investigation list | rows rise 6 px and fade in, staggered 90 ms after a 380 ms pause | `cubic-bezier(.2,.7,.1,1)` |
| Open drain 14: tilt | `flyTo` 1,400 ms, pitch 58°, bearing along drain to dump | MapLibre default curve 1.2 |
| One-truck replay | 3,600 ms, camera follows the head, live "driven km" readout | ease in-out cubic |
| Shortfall | dimension line draws 600 ms while zooming out | ease out |
| Settle | map frame morphs into Exhibit A in 1,100 ms while pitch and bearing return to 0; exhibits fade in staggered 0, 120, 240 and 360 ms; a 350 ms final ease lands the framing | ease in-out |
| Confirmation | rises 8 px in 220 ms | `cubic-bezier(.2,.7,.1,1)` |
| Saving | spinner, at least 800 ms (prototype) | |

`prefers-reduced-motion` collapses all of it to the end state: tweens jump to
1 and CSS durations drop to 1 ms.

## Loading, empty and error states

- **Loading:** exhibits show skeleton bars while a drain loads, and the map
  keeps its last frame. Run verification is disabled until the bill loads.
- **Missing slip image:** an amber note says the fields Textract read are
  shown and the trip is in review, not held.
- **Expired photo link:** the five-minute links expire, so "Photo link
  expired. Reopen the drain" appears, and the app should refetch the drain.
- **Save failed:** the decision bar error described under Components.
- **No API (offline):** the existing snapshot mode, with the disclosure.
- **No Amazon Location:** the existing self-contained fallback style, already
  recoloured in the prototype.

## Responsive behaviour

The target is 1920×1080. Down to 1440×900 the same grid holds:
- the sheet narrows to 440 px;
- the case right column goes to 640 px, with Exhibit B's fields in one column.

Below 1280 px the case file stacks as A, B, C, then the decision bar, which
becomes sticky at the bottom. The overview puts the map above the sheet. Both
prototypes use fixed pixel tracks, so this is specified rather than built.

## Accessibility

- Every control is a real button with a visible focus ring.
- The rings are focusable markers with `aria-label`.
- The comparison slider is `role="slider"` with arrow-key steps.
- Confirmation sheets are `role="dialog"`, and focus moves into them.
- The ledger bar has a text `aria-label`.
- Colour is never the only signal: stamps say HELD, REVIEW and APPROVED in
  words, and the timeline marks bad events in red *and* with the words.
- The production build adds:
  - Escape to close a confirmation;
  - returning focus to the opener;
  - announcing "Decision saved" through an `aria-live` region.

## Simulated-data labelling

| Where | Label |
|---|---|
| First screen | "Simulated demonstration. The bill, contractor, weighbridge slips, GPS traces and photos are generated. No real ward or payment is involved." |
| Everywhere | "Simulated data" badge |
| Photos | "Simulated photo" on each image; the real-photo slot says "Not yet supplied" |
| Slip | "Sample slip" tag, plus the slip's own SAMPLE DATA mark and footer |
| Offline Bedrock text | "Offline summary, no model was called" / "a canned sample, no model called" |

## Screenshots

| File | State |
|---|---|
| `01-pre-verification.png` | Neutral drains, ₹22.32 lakh claim, "Not yet calculated", Run verification |
| `02-verified.png` | ₹6.66 lakh with HELD stamp, ledger, investigation list with stamps |
| `03-drain-14.png` | The case file |
| `03b-drain-14-real-photo-slot.png` | Exhibit C with the reserved real-photo slot |
| `04-hold-confirmation.png` | Hold confirmation |
| `05-approve-confirmation-review-drain.png` | Approving review drain 8 |
| `05b-approve-confirmation-drain-14.png` | Approving against the evidence: site note required, button disabled |
| `05c-decision-saving.png` | Submitting |
| `05d-decision-error.png` | Save failed |
| `06-decision-completed.png` | Drains 6 and 8 approved, 14 held: 870 t verified, 0 review, 370 t held, ₹6.66 lakh |
| `06b-drain-14-held.png` | Drain 14 after the hold |
| `07-components.png` | Colour, type, buttons in every state, stamps, symbols, confirmation, done, loading, errors |
| `08-hover.png` | Hover on drain 3: row, ring and tooltip |
| `closeup-exhibit-*.png` | Exhibits A, B, C and the photo slot at the size they appear |

Videos (regenerated locally, not committed): `videos/hybrid/01-verification-scan.webm` (5.7 s) and
`02-open-drain-14.webm` (9.3 s). Both are a real MapLibre GL map running in
Chrome with SwiftShader. The camera moves are MapLibre `flyTo`, `jumpTo` and
`easeTo` calls, not CSS. They were recorded with CDP screencast.

## Implementation risks

- **MapLibre version.** The prototypes use v5 from a CDN; the app has v6. The
  calls used (`flyTo`, `cameraForBounds`, `Marker`, `addImage`) exist in v6,
  but check marker opacity handling: MapLibre overrides a marker element's own
  `opacity`, so toggle a child.
- **Frame morph cost.** `map.resize()` every frame is heavy on software GL. On
  the recording laptop with a GPU it is smooth. The fallback is to cross-fade
  the overview map into a second case map instead of morphing one.
- **Camera framing per ward.** `cameraForBounds` with fixed padding fitted
  this geometry. A ward with a long east-west haul needs the padding checked;
  the camera is derived, not hard-coded.
- **Live summaries.** The live summary is two model sentences and may be longer
  than the offline text. The bar clamps it to three lines; the full text goes
  in a tooltip.
- **Image links expire in 5 minutes.** An engineer who leaves the case open
  will see broken images. The app should refetch on `error` from an `<img>`.
- **Plex Mono** is a new font dependency, or a self-hosted file.
