# Travel handoff: resuming SiltProof from GitHub

Written 9 Oct 2026 on the Mac, before moving to the Durgapur laptop. This file
and the git history are the whole record. Nothing below depends on that Mac.

## Where things are

- **Repo:** `https://github.com/Somsubhra-Nandi/siltproof`
- **Branch:** `feat/hybrid-frontend`, branched from `main` at `347a4f1`.
  Not merged, and nothing is deployed.
- **Implementation head:** `ea573a9`
  ("docs(frontend): run instructions, capture commands and retired
  screenshot script").
- This handoff document is committed on top of `ea573a9`. To find the exact
  tip, run `git log -1 origin/feat/hybrid-frontend`.
- `main` is unchanged at `347a4f1`.

Commits on the branch, oldest first:

| SHA | What |
|---|---|
| `c28496d` | Survey-sheet tokens, fonts, basemap palette, contrast fixes |
| `5323d40` | Pure modules: `lib/ledger`, `lib/caseFacts`, `lib/timeline` and tests |
| `30ba7be` | Overview sheet, survey map, case file, three exhibits, decision bar, confirmation |
| `99c63a6` | The drain-opening flight and the verification sweep |
| `76ad67b` | Amazon Location Monochrome Light basemap, survey tint, mock check |
| `962dc8e` | Fixes: case framing, slip highlight, photo slots, decided layout; capture tooling |
| `ea573a9` | Frontend README, capture commands, old screenshot script retired |

## Implementation status

The approved hybrid design (`design/DESIGN.md`, prototype
`design/hybrid/index.html`) is implemented in `frontend/src`. It was checked
in headless Chrome against the production build, on the offline snapshot.

**Done:**

- **Pre-verification:**
  - neutral dashed rings and the ₹22.32 lakh claim;
  - "Not yet calculated" in the three money slots;
  - the Run verification button;
  - no verdicts before the press.
- **Verification sweep:** a line moves down the map and reveals the money and
  each drain as it passes, with "Checking trip n of 117". Then the HELD stamp
  lands and the decision list rises.
- **Verified overview:**
  - ₹6.66 lakh held, and the ledger bar at 805 / 65 / 370 t;
  - stamped rows, each with a one-line reason from its findings;
  - rings whose area is tonnes, held tags, graticule ticks, the title block,
    the off-map dump pointer;
  - a hover tooltip synced with the list.
- **Case file for any drain:**
  - **Exhibit A:** the one map moves into it, with the timeline.
  - **Exhibit B:** the generated slip, a loupe, the Textract fields with
    confidence, and the rule message.
  - **Exhibit C:** a comparison slider, pHash grids, the R3 finding, and an AI
    observation that is styled and labelled differently from findings.
  - Exhibit titles are derived from the drain's findings.
- **R8 honesty:** slip time-in 06:53, GPS departure 07:12 and last fix 07:37
  are separate events, followed by "Never arrives at the dump site". A last
  fix is never shown as an arrival. Tests check the 19 and 44 minute brackets
  against the finding text.
- **Drain 14 flight:** tilt, then a replay of one trip, then the 2.2 km
  dimension line, then the map settles into Exhibit A, about 5.5 s in all.
  The claimed route, the trail and the dump site stay visible throughout.
- **Decisions:**
  - Hold and Approve, each with a confirmation showing the money moves;
  - saving, error ("Not saved", bill unchanged) and decided states;
  - "Change decision";
  - approving against hard findings needs a site note of 20 or more
    characters (UI rule only).
  - Approving review drains 6 and 8 gives **870 / 0 / 370 t, ₹6.66 lakh**.
- **Evidence links:** `imageUrl` and `slipImageUrl` are used when present.
  Links are refetched on an image error (20 s cooldown) and before they
  expire, and are only ever held in memory. Missing images get labelled
  slots.
- **Accessibility:**
  - reduced motion skips straight to the end state;
  - Escape closes dialogs and focus returns to the opener;
  - decisions are announced in an `aria-live` region;
  - rings, rows and the slider work from the keyboard.
- **Amazon Location:**
  - Maps v2 Monochrome Light, configurable;
  - background and water are tinted to the survey palette, and attribution
    is shown;
  - the offline fallback is automatic.
  - It was tested only against a mocked endpoint (see below).

**Captures from the real app:**

- `frontend/screenshots/app/` holds the DESIGN.md states, the other drains,
  and 1440, 1280 and 390 px widths.
- `frontend/videos/01-verification-scan.webm` is 6.3 s.
- `frontend/videos/02-open-drain-14.webm` is 7.1 s.

## Verified results (9 Oct 2026, on the Mac)

| Check | Result |
|---|---|
| `cd frontend && npm test` | 76 / 76 passed (was 46 before the redesign) |
| `npm run lint` | clean |
| `npm run build` (`tsc -b && vite build`) | builds |
| `.venv/bin/python -m pytest` | passed, exit 0 (319 tests; no backend code changed) |
| `npm run check:location` (mocked Amazon) | 7 / 7 checks pass |
| `npm run capture` against `vite preview` | all stills and both videos written |

## Known issues and gaps

- **No independent whole-branch code review yet.** A reviewer was started and
  then stopped before it reported. Only a self-review exists. Do this first
  (see Next steps).
- **Real Amazon Location tiles have never loaded.** `surveyTint` matches
  layers by name (`background`, and anything named water, river, lake or
  canal). The real Monochrome layer ids may differ, so the tint may need
  tuning.
- The design's component-sheet page (`07-components`) was not built.
- Below 1100 px the overview stacks the map above the sheet. It works but is
  not polished.
- The flight plays for any drain whose focus trip failed R5, which is drains
  14 and 16 in the seed data. On drain 16 the focus trip is 002, which
  reached the dump, so in practice only drain 14 flies.
- The "stops 07:33" event is the trip's `arrivalTime` from the generator,
  which is the end of the trace minus the dwell time. It is kept separate
  from the 07:37 last fix.
- The approval preview starts from the drain's current row. After a decision
  is changed, the preview can differ from what the backend computes from the
  evidence.
- `?simulate=save-error` (offline only) forces a save failure, so the error
  state can be shown.
- `design/node_modules` is a dead symlink to a temporary folder on the Mac. It
  is git-ignored and nothing needs it. The new capture tool uses
  `frontend/node_modules/puppeteer-core`.

## Laptop setup

Prerequisites:

- Node 24 (the Mac used v24.19.0)
- Python 3.12 or 3.13
- Git
- Google Chrome, only for the capture scripts
- Docker, only for `sam build --use-container`

```bash
git clone https://github.com/Somsubhra-Nandi/siltproof.git
cd siltproof
git checkout feat/hybrid-frontend
git log -1 --oneline                      # should match origin/feat/hybrid-frontend

# backend tests (offline: moto + MOCK_AWS)
python3 -m venv .venv
source .venv/bin/activate                 # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest                          # about 80 s; regenerates data/out itself

# frontend
cd frontend
npm ci
npm test && npm run lint && npm run build
npm run dev                               # http://localhost:5173
```

On Windows, the capture scripts need `CHROME_PATH` set to `chrome.exe`, and
`FFMPEG` set to an ffmpeg with libvpx for the videos. The app and the tests
need neither.

### Offline demo mode (no AWS at all)

`frontend/.env` does not exist in git. Without `VITE_API_BASE_URL` the app
runs on the committed snapshot in `frontend/public/data/demo/`, and without a
Location key it uses the bundled basemap. That is the whole demo:

- `/` opens the unchecked bill. Press **Run verification**, then click drain 14.
- `/?state=verified` opens the verified overview.
- `/?state=verified&drain=14` opens the case file directly.
- `&photos=slot` shows the reserved frames for real photos.

Decisions offline are applied in the browser with the backend's arithmetic,
and reset on reload. If the generators or rules change, regenerate the
snapshot with `python scripts/make_demo_fixtures.py`.

Captures need Chrome:

```bash
npm run build && npx vite preview --port 4173 --strictPort
npm run capture          # screenshots/app/*.png and videos/*.webm
```

## Environment variables (names only, never commit values)

**`frontend/.env`** (template: `frontend/.env.example`). These are also the
Amplify variables.

- `VITE_API_BASE_URL`: the API Gateway URL from `sam deploy`. Empty means the
  offline snapshot.
- `VITE_BILL_ID`: defaults to `B1`.
- `VITE_AWS_REGION`: `ap-south-1`.
- `VITE_LOCATION_API_KEY`: the Amazon Location browser key. Empty or
  `replace-me` means the offline map.
- `VITE_LOCATION_MAP_STYLE`: defaults to `Monochrome`.
- `VITE_LOCATION_COLOR_SCHEME`: defaults to `Light`.
- `VITE_MAP_CENTER_LAT`, `VITE_MAP_CENTER_LON`, `VITE_MAP_ZOOM`: the starting
  view.

**Root `.env`** (template: `.env.example`), used by the scripts and seeding:

- `AWS_REGION`
- `BEDROCK_MODEL_ID`
- `EVIDENCE_BUCKET`
- `TABLE_NAME`
- `API_URL`

**`infra/samconfig.toml`** (template: `infra/samconfig.toml.example`):

- `stack_name`, `region`, `capabilities`, `parameter_overrides`

Copy the root `.env` and `infra/samconfig.toml` values from the Mac or the
AWS console. They are git-ignored on purpose and are **not** on GitHub. Only
`EVIDENCE_BUCKET`, `TABLE_NAME` and `API_URL` matter, and they come from the
stack outputs (`sam list stack-outputs` or the CloudFormation console).

## AWS status

- No AWS call or change was made in this session.
- The Mac has a filled-in root `.env` (with `API_URL`) and an
  `infra/samconfig.toml`, so a stack was deployed at some point. Its current
  state was **not** checked. Check it in the console before relying on it.
- **Pending live steps** (`DECISIONS.md`, "Pending live steps"; each needs
  approval):
  1. Redeploy the api and ingest code:
     `sam build --use-container --template infra/template.yaml && sam deploy --config-file infra/samconfig.toml`.
     This ships the `imageUrl` and `slipImageUrl` links and the new R8 wording.
  2. Re-upload the regenerated slips if the bucket was seeded before that
     change. Each upload calls Textract once (drain 14 is 18 slips). Run
     `python data/gen_slips.py --check` first, and use the seed dry run to
     list the billable calls.
  3. `POST /verify/B1` (rules only, no model calls), so the stored R8
     messages pick up the new wording.
  4. Optional: `POST /drain/14/summary` with `{"refresh": true}` refreshes
     the cached summary, and **calls Bedrock**.
- Until step 1 runs, the live API returns no image links. The frontend
  handles that by showing labelled slots.

## Amazon Location (untested against real tiles)

Full guide: `docs/amazon-location.md`. In short:

1. In the console, create an API key in `ap-south-1` with **Maps only**: style
   descriptor, tiles, glyphs and sprites (`geo-maps:GetStyleDescriptor`,
   `GetTile`, `GetGlyphs`, `GetSprites`; confirm the names in the console).
2. Restrict referrers to the Amplify URL, any custom domain, and
   `http://localhost:5173/*` and `http://localhost:4173/*`. Set an expiry.
3. Put the key in `frontend/.env` as `VITE_LOCATION_API_KEY`, run
   `npm run dev`, and look at the result:
   - no "Offline basemap" note;
   - 200s for the descriptor and tiles;
   - Amazon's attribution;
   - the overlays drawn above the tiles.

   Only then call it verified. Tile requests are billable.
4. Without AWS, `npm run check:location` exercises the wiring against a test
   double (see the doc for the second dev server it needs).

## Amplify Hosting requirements

- App root `frontend`, build `npm ci && npm run build`, output `dist`.
- Set the `VITE_*` variables above as Amplify environment variables, and
  **redeploy after changing any of them**, because they are baked in at build
  time.
- No rewrite rules are needed: all state is in the query string.
- Add the Amplify origin to the Location key's referrer list.
- Without `VITE_API_BASE_URL`, the hosted site runs the offline snapshot,
  which is still a full demo.

## Integrating the teammate's judge-upload branch

That branch is not on GitHub yet; only `main` and this branch exist.

**Base the work on this branch**, not `main`:

```bash
git fetch origin
git rebase origin/feat/hybrid-frontend
```

Or merge it in. Either way, resolve the frontend conflicts in favour of the
new structure.

**What changed under them:**

- `SummaryBar.tsx` and `DrainPanel.tsx` are deleted.
- `App.tsx` was rewritten. It now owns the state for the bill, the sweep,
  the open drain, decisions, and the map frame.
- Overview UI is in `components/BillSheet.tsx`.
- Per-drain UI is in `components/CaseFile.tsx` and the Exhibit components.
- `api.ts` behaviour is unchanged. Its arithmetic moved to `lib/ledger.ts`.
- `types.ts` gained optional fields only.

**Where an upload feature fits:**

- Put API calls in `api.ts`, next to the others.
  - Keep an offline branch, so offline mode still works: for example, a
    disabled control labelled "Upload needs the API".
  - The existing `upload-url` route (presigned PUT) is the backend seam.
- **UI placement:** an upload entry point fits the masthead or the bill meta
  in `BillSheet`. Uploaded evidence fits the relevant exhibit.
- **Styling:** use the tokens in `src/index.css` and the `.btn`, `.stamp`,
  `.err` and `.warn` classes. Keep text at 15 px or more, and label
  simulated or judge-supplied data explicitly. Never style AI output like a
  rule finding.
- **Heavy AI stays at ingestion.** An upload triggers the S3 ingest Lambda.
  Never call Bedrock or Textract from the page.

**Before merging:**

- `npm test`, `npm run lint` and `npm run build` all pass.
- The backend `pytest` passes.
- Run `npm run capture` and look at the screenshots.

## Local resources not on GitHub

| What | Where on the Mac | Needed? |
|---|---|---|
| Root `.env` | `siltproof/.env` | Only for seeding and live scripts. Rebuild from stack outputs |
| `infra/samconfig.toml` | `siltproof/infra/` | Only to deploy. Rebuild from `samconfig.toml.example` |
| `data/out/` (15 MB) | generator output | No. Regenerated by `data/gen_*.py` and by the oracle test |
| `.superpowers/sdd/...` | execution ledger | No. Its rulings are copied below |
| `design/node_modules` | dead symlink | No |
| `frontend/scripts/_shot.mjs`, `_frames.mjs` | ad-hoc screenshot helpers | No. `capture.mjs` replaces them |

Rulings from the execution ledger, so they are not lost:

- **One map:** a single map instance moves between the overview and Exhibit
  A, using measured rectangles. If it stutters on a slow GPU, cut to the case
  layout and fit the bounds instead.
- **Flight:** tilt 1000 ms, replay 2600, dimension line 500, hold 400, morph
  1000. The camera frames the whole haul rather than following the truck.
- **Overview confirmations:** confirmations in the overview list render in
  flow, not floating, so they can't be clipped.
- **Amazon Location tint:** applied with `setPaintProperty` after the style
  loads, not `transformStyle`, so the existing error and timeout fallback is
  unchanged.
- **Commit granularity:** milestones M2 to M7 landed as one commit, because
  they share one new state model.

## Next steps for a fresh Claude Code session

1. **Clone and set up** (above). Check that `npm test`, `npm run lint`,
   `npm run build` and `pytest` pass.
2. **Run the independent review that is still pending.** Run `/code-review`,
   or a reviewer, on `main...feat/hybrid-frontend` (diff `347a4f1..HEAD`).
   Focus on:
   - `App.tsx` races between opening a drain, the flight and mode changes;
   - the morph layout effect;
   - the decision money (`lib/ledger.ts` against
     `backend/common/rules.py`'s `apply_decision`);
   - R8 labels in `lib/timeline.ts` and `MapView`;
   - the evidence link refresh;
   - MapView listeners across a style swap.

   Fix Critical and Important findings test-first.
3. **With approval:** create the Amazon Location key and verify real tiles;
   tune `surveyTint` if the layer names differ.
4. **With approval:** run the pending live steps above (redeploy, slip
   re-upload, re-verify), then test live mode with `VITE_API_BASE_URL`.
5. Set up Amplify. Bring in the judge-upload branch as described above.
6. Open a PR from `feat/hybrid-frontend` to `main` once it is reviewed. Don't
   merge without the team.

Rules that still apply (see `CLAUDE.md`):

- Ask before anything that creates AWS resources or costs money.
- Never commit secrets.
- After changing rules or generators, regenerate the snapshot.
