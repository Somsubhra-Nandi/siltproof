# Hybrid redesign: implementation plan (about 10 hours)

This plan is for the existing app in `frontend/src`: React 19, Vite,
maplibre-gl 6, and vitest with jsdom. Run screenshots with
`npm run screenshot` (puppeteer-core and SwiftShader).

**Implemented on branch `feat/hybrid-frontend` (October 2026).** Deviations are listed in the SDD ledger rulings and the final handover.

## Ground rules

- **No new runtime dependencies.** Everything is MapLibre layers, HTML
  markers and CSS. The only new asset is IBM Plex Mono, self-hosted as a
  woff2 or loaded from Google Fonts.
- **Unchanged:**
  - `api.ts` request and response handling;
  - offline snapshot mode;
  - the offline `applyDecision`;
  - basemap mode selection and the fallback style;
  - R1 to R10 and every total.
- **After every milestone:**
  - `npm test`, `npm run lint` and `npm run build` pass;
  - `npm run screenshot` is refreshed;
  - the app still demos end to end, offline and live;
  - one small commit.
- **Reference:** the prototype `design/hybrid/index.html` is the source for
  CSS values and layout logic, ported rather than copied wholesale.

## Today's structure

| File | Role today | Becomes |
|---|---|---|
| `App.tsx` (184 lines) | Layout, state, verify, select, decide | Same state; two layouts (overview, case) |
| `components/SummaryBar.tsx` | Dark KPI header | `BillSheet.tsx`: the left working sheet |
| `components/MapView.tsx` (383) | Map, drains, routes, colour-in | Survey styling, rings, sweep, case layers, camera |
| `components/DrainPanel.tsx` (319) | One long panel | `CaseFile.tsx` with `ExhibitRoute`, `ExhibitSlip`, `ExhibitPhotos`, `DecisionBar` |
| `basemap.ts` | Fallback colours | Survey palette |
| `types.ts` | API types | `+ imageUrl?`, `slipImageUrl?`, `evidenceUrlExpiresInSeconds?`, `mocked?`, R8 `arrivalKind?` / `departure?` |
| `App.css`, `index.css` | Styles | Token sheet plus component CSS |

## Milestones

### M1. Tokens, type and the shell (1 h)

- **Files:** `index.css`, `App.css`, `index.html` (fonts), `App.tsx` (grid
  only), `basemap.ts` (colours).
- **Work:**
  - Add the palette and type tokens from DESIGN.md as CSS variables, with the
    production contrast fixes.
  - Set the 500 px sheet plus map-frame grid.
  - Recolour `fallbackStyle` and the verdict colours.
- **Preserve:** every existing component renders where it is.
- **Depends on:** nothing.
- **Accept when:**
  - the app looks warm and paper-like;
  - all 46 vitest tests pass;
  - the basemap tests still prove the offline style makes no network request.
- **Tests:** update the `basemap.test.ts` colour expectations; distinctness
  stays.
- **Screenshot:** overview at 1920×1080.
- **Demonstrable:** today's app in the new skin.

### M2. Pre-verification sheet (1 h)

- **Files:** new `BillSheet.tsx` (replaces `SummaryBar.tsx`), `format.ts`.
- **Work:**
  - The disclosure, bill meta and the ₹22.32 lakh claim.
  - "Not yet calculated" slots.
  - The 60 px Run verification button with busy and disabled states.
  - The 18-drain grid.
- **Preserve:**
  - `pendingView`;
  - `?state=verified`;
  - the API-failure banner;
  - the missing-evidence warning.
- **Depends on:** M1.
- **Accept when:** nothing about verdicts shows before the press, and the
  button shows "Checking trip n of 117" while busy.
- **Tests:** port "holds back the verdict columns until verification has run"
  and "goes from an unchecked bill to a verified one" to the new markup.
- **Demonstrable:** the opening screen of the video.

### M3. Verified sheet and money (1 h)

- **Files:** `BillSheet.tsx`, new `Stamp.tsx`, `format.ts`.
- **Work:**
  - The held figure with the HELD stamp.
  - The claimed row, ledger bar and three keys (mono).
  - The "Needs your decision" list with HELD and REVIEW stamps and one-line
    reasons from the first finding.
  - Inline approve and hold for review drains, with a confirmation sheet.
- **Preserve:** `decide()` and the summary recomputation after a decision.
- **Depends on:** M2.
- **Accept when:** approving drains 6 and 8 shows 870 t verified, 0 review,
  370 t held, ₹6.66 lakh, from `applyDecision`.
- **Tests:** port "updates the summary bar when a drain is approved", and add
  an 870/0/370 test for approving both review drains.
- **Demonstrable:** the whole money story without the map changes.

### M4. Survey-sheet map (1 h)

- **Files:** `MapView.tsx`, new `MapFurniture.tsx` (neatline, ticks, north
  arrow, title block, scale, off-map pointer).
- **Work:**
  - Ring markers (area is tonnes) and held tags.
  - Hover sync with the list.
  - Graticule ticks hidden when the map is pitched.
- **Preserve:**
  - Amazon Location versus fallback;
  - the style timeout fallback;
  - drain click selects.
- **Depends on:** M1.
- **Accept when:**
  - ring legend reads "Ring area is tonnes billed, 42 t to 192 t";
  - no ring or tag appears before verification;
  - toggle a marker's **child** element, not its own opacity, which MapLibre
    overrides.
- **Tests:** none in jsdom (the map is stubbed). The check is a screenshot.
- **Demonstrable:** the overview matches `02-verified.png`.

### M5. Case file shell (1 h)

- **Files:** new `CaseFile.tsx`, `App.tsx` (case layout), `DrainPanel.tsx`
  retired.
- **Work:**
  - The header with the amount, stamp and whole-bill box.
  - Grid placeholders for Exhibits A, B and C.
  - The decision bar wired to the existing `decide()`, with confirmation,
    saving, done and error states.
  - Back to all drains.
- **Preserve:**
  - `?drain=14` deep link;
  - the note sent with the decision;
  - "will not let the engineer decide before verification";
  - the evidence summary display.
- **Depends on:** M3.
- **Accept when:**
  - hold and approve both work offline and against the live API;
  - the error state appears when `decide()` rejects.
- **Tests:** port the drill-down tests ("names every failed rule", "passes the
  note", "shows a decision once taken"). Add a test that approval against hard
  findings stays disabled until a site note exists.
- **Demonstrable:** every decision on the bill, with the evidence still as text.

### M6. Exhibit A: route map and timeline (1.5 h)

- **Files:** `MapView.tsx` (case mode: claimed, actual, others, stop, the
  hatched dump geofence, dimension line, labels), new `CaseTimeline.tsx`.
- **Work:**
  - The map container moves into Exhibit A's slot; one map, resized.
  - Fit with `cameraForBounds`.
  - Build the timeline from `trip.startTime`, `arrivalTime`,
    `slip.timeIn/timeOut` and R8's `evidence.arrival` / `departure`.
  - Lay out labels with the measured placement from the prototype.
- **Preserve:** route visualisation for every drain, not just 14. Drains
  without an R8 finding show the GPS lane only.
- **Depends on:** M5, M4.
- **Accept when:**
  - no label overlaps at 1920 or 1440 wide;
  - the R8 bracket's minutes match the finding message.
- **Tests:**
  - the label placer as a pure function, with fixtures for 4-minute-apart
    events and an event at the axis end;
  - minutes in the brackets equal the message's.
- **Demonstrable:** the key shot without motion.

### M7. Exhibits B and C (1 h)

- **Files:** new `ExhibitSlip.tsx`, `ExhibitPhotos.tsx`.
- **Work:**
  - The slip from `trip.slipImageUrl`, with the highlight and loupe. Row
    positions come from the generator layout; keep them in one constant.
  - The Textract fields with confidences, and the R8 message.
  - The photo comparison slider and hash grids.
  - The R3 finding box, then the AI observation box with provenance (shows
    "canned sample" when `bedrock.mocked`).
  - The real-photo slot when a link is null.
  - Refetch the drain when an `<img>` errors, since links expire in 5 minutes.
- **Preserve:** the slip table data (the fields are the same).
- **Depends on:** M5. The backend `imageUrl` change works offline already;
  live it needs the redeploy.
- **Accept when:**
  - offline drain 14 shows the regenerated slip and both photos;
  - a drain with null links shows the labelled slot, not a broken image.
- **Tests:**
  - null links render the slot;
  - `mocked` photos render "no model called";
  - the conflicting field is highlighted (port the existing test).
- **Demonstrable:** the full case file, static.

### M8. Motion (1 h)

- **Files:** `MapView.tsx`, `BillSheet.tsx`, `App.tsx`.
- **Work:**
  - The verification sweep driven by the existing count-up, revealing drains
    by screen y.
  - The ₹6.66 lakh landing and stamp.
  - List stagger.
  - Open drain 14: tilt, one-truck replay with km readout, dimension line,
    then settle. Timings are in DESIGN.md.
  - `prefers-reduced-motion` respected throughout.
- **Preserve:** `animate` stays off in tests; the existing colour-in fallback.
- **Depends on:** M4, M6.
- **Accept when:** both sequences play under 10 s on the recording machine and
  can be skipped by clicking.
- **Tests:** reduced motion jumps straight to the end state (jsdom with
  `matchMedia` stubbed).
- **Demonstrable:** the video.

### M9. Polish, responsive and recording readiness (1.5 h)

- **Work:**
  - 1440×900 and 1280 layouts.
  - Escape and focus return on confirmations, plus an `aria-live` decision
    announcement.
  - The empty and expired-link states.
  - Refresh all screenshots.
  - Run `npm test`, lint and build, and the full backend suite.
  - Do a dry run of the three-minute script, offline and live.
- **Accept when:** DESIGN.md's screenshot list can be reproduced from the app
  itself.

**Total:** M1 to M9 is 10 hours.

## If time runs short, drop in this order

1. The frame morph. Cut to the case layout and `fitBounds` instead (saves 30
   min and removes the riskiest animation).
2. The one-truck follow camera. Draw the trace progressively while flat (30
   min).
3. The photo comparison slider. Show the photos side by side (20 min).
4. Graticule ticks and the off-map pointer (20 min).
5. The loupe on the slip. Highlight on the slip image only (15 min).
6. Responsive layouts below 1440 px. The demo machine is 1920 (45 min).

The floor, if only M1 to M5 and M7 land (7 hours), still has every workflow,
the new money hierarchy, stamps, the case file and real evidence images.

## Backend needed before the live demo (approval required)

The redeploy and data steps are in `DECISIONS.md` under "Pending live steps".
The frontend falls back gracefully without them: null image links show the
slot, and old R8 text still renders.
