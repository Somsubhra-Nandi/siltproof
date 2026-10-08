# Hybrid: decisions taken overnight without you

You asked me not to wait for approval at intermediate design stages. These are
the calls I made instead. Each is cheap to reverse.

## Process

- **Brainstorming classification: architectural for the eventual build,
  design-only tonight.** Your brief already fixed the direction and the
  constraints, so I did not ask the clarifying questions the brainstorming
  process would have. The brief is the agreed understanding. The written
  plan is `IMPLEMENTATION-PLAN.md`; it needs your approval before any of
  `frontend/src` changes.
- **Order of work** followed your priority list: evidence fixes, image links,
  the hybrid's three main states, captures and docs, then the plan.

## Data and backend

- **R8 wording.** I kept R8's comparison (slip time-in against the GPS arrival
  at the dump site, or the last fix when there is none) and named both times
  in the message. I added the departure clause because it is the contradiction
  a judge grasps fastest; the tolerance still applies only to the arrival
  comparison. The 40 versus 44 drift was the generator's stop time versus the
  rule's last fix, 4 minutes of dwell apart. I changed neither number, only the
  explanation.
- **Mock attribution.** "Correct stale Haiku attribution" could have meant
  "write Nova Pro". I chose `modelId: null, mocked: true` for every offline
  photo check and summary, because no model produced them. Live records still
  carry the real model id.
- **The offline summary** is now assembled from the drain's own findings.
  Before, all six flagged drains showed drain 14's canned sentence.
- **Image link field names** are `photos[].imageUrl`,
  `trips[].slipImageUrl` and `evidenceUrlExpiresInSeconds`. A slip link sits
  on the trip rather than inside `slip`, because `slip` is null when the slip
  was never ingested, and the link must be null then too.
- **The offline snapshot carries drain 14's images** (18 slips, 4 photos and
  drain 9's reused original, 1.2 MB) as local paths. Every other drain's links
  are null offline. It is enough for the demo without bloating the repo.
- **No HEAD request per image** to check existence. The evidence item in
  DynamoDB is the existence check; an object deleted after ingestion shows as
  a broken image, which the UI should treat as "link expired or missing".

## Design

- **The stand-in photograph was removed.** Phase 4 asked for a realistic
  muddy-drain stand-in. Before your addendum arrived, I had downloaded one
  CC BY-SA photo from Wikimedia Commons into `design/hybrid/assets/`. Per the
  addendum, I deleted it before any commit and replaced the idea with a
  reserved, labelled slot (`?photos=slot`). **The question Phase 4 asked, how
  the warm palette holds up against natural photo colour, is still open** until
  your real photos arrive. Drop them in and recapture `03` and `03b`.
- **No register on the case file.** C's compressed bill column made
  Direction A's layout crowded again. The case header carries the whole-bill
  totals instead, so a decision's effect on the bill is still visible.
- **Review drains are decided inline** in the overview list (drains 6 and 8),
  not in a full case file. Their evidence is one soft finding each. This is
  also the path to the plan's 870 t headline.
- **Approving against hard evidence requires a site note** of at least 20
  characters in the confirmation, and the button stays disabled until then.
  The backend does not enforce this; it is a UI rule. Drop it if it slows the
  video.
- **One truck in the replay** (trip 001), as the brief says, with the other
  17 traces drawn faintly afterwards so "every trace stops here" is visibly
  true.
- **Plex Mono for tabular figures.** The brief asked for tabular monospace
  numerals. Running text keeps Instrument Sans tabular figures, because Plex
  in a sentence reads as code.
- **No scrim behind confirmations.** A full-page scrim could not sit between
  the map and the case cards without hiding the map, so confirmations rely on
  a strong shadow and an anchored position instead.
- **Contrast.** Review amber and the tertiary grey fail 4.5:1 for small text.
  DESIGN.md gives darker text tokens for production; the prototype is
  unchanged.

## Not done, or blocked

- **Real photographs:** waiting on yours.
- **Live redeploy, slip re-upload, re-verify and summary refresh:** listed in
  `DECISIONS.md` ("Pending live steps") with exact commands. None has run.
- **Responsive layouts** below 1440 px are specified in DESIGN.md, not built
  in the prototype.
- **Production React implementation:** not started, by instruction.
