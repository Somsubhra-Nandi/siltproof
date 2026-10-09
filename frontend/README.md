# SiltProof frontend

React 19, Vite and MapLibre GL 6. It implements the approved hybrid design
(`../design/DESIGN.md`): a survey-sheet overview, then a three-exhibit case
file for each drain.

## Run the demo

```bash
npm ci
npm run dev                      # http://localhost:5173
```

With no `VITE_API_BASE_URL`, the app runs on the offline snapshot in
`public/data/demo`, and with no Amazon Location key it uses the bundled
basemap. So this is the complete demo with no AWS at all. Useful URLs:

| URL | Opens |
|---|---|
| `/` | The unchecked bill. Press **Run verification** |
| `/?state=verified` | The verified overview (offline snapshot only) |
| `/?state=verified&drain=14` | Drain 14's case file, no flight |
| `&photos=slot` | Exhibit C with the reserved frames for real photos |
| `&simulate=save-error` | Offline only: the next decision fails, to show the error state |

From the overview, click drain 14 in the list or on the map to play the
flight.

Live mode: copy `.env.example` to `.env` and set `VITE_API_BASE_URL` (the
`ApiUrl` from `sam deploy`). For the real basemap, also set the Amazon
Location variables; see `../docs/amazon-location.md`.

## Checks

```bash
npm test                         # vitest + jsdom
npm run lint
npm run build                    # tsc -b && vite build
```

Screenshots and videos of the real app (needs Chrome):

```bash
npm run build && npx vite preview --port 4173 --strictPort
npm run capture                  # screenshots/app/*.png and videos/*.webm
```

Amazon Location wiring against a test double (no AWS call):

```bash
VITE_AWS_REGION=ap-south-1 VITE_LOCATION_API_KEY=mock-key npx vite --port 5182 --strictPort
npm run check:location
```

## Layout

- `src/App.tsx` holds the state: the bill, the verification sweep, the open
  drain, decisions, and the one map frame that moves between the overview
  and Exhibit A.
- `src/components/` contains `BillSheet` (overview sheet), `MapView`
  (survey map, rings, sweep, case layers, flight), `CaseFile` with
  `CaseTimeline`, `ExhibitSlip`, `ExhibitPhotos` and `DecisionBar`, and
  `ConfirmSheet` and `Stamp`.
- `src/lib/` holds pure, tested logic:
  - `ledger` is decision arithmetic and previews, the same as the backend;
  - `caseFacts` covers exhibit facts and titles from findings;
  - `timeline` builds the R8-aware lanes and places labels;
  - `slipLayout` holds the generated slip's row positions;
  - `motion` handles tweens and reduced motion.
- `src/api.ts` is the API client and offline snapshot, unchanged in
  behaviour.
