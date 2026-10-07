# SiltProof: 3-Day Hackathon Plan

**Environmental Hacks (AWS) · 8–10 Oct 2026 · Track: Heat and Water**

> **One-liner:** We don't show the city its drains. We tell the engineer exactly how much of the desilting bill to pay, and show the evidence for every rupee we hold.

"SiltProof" is a working name. Rename it if you like.

---

## 1. What we are building (and not building)

**The single decision:** a ward engineer approves or holds payment for each drain section in a contractor's desilting bill.

### In scope (MVP)
- One ward, one contractor bill, 18 drain sections, about 120 dumper trips
- An ingestion pipeline: evidence lands in S3 and AWS extracts the facts (Textract, Bedrock, EXIF, perceptual hash)
- A verification engine: 10 deterministic rules turn each trip into verified, review or hold
- **One decision screen:** a ward map coloured by verdict, a summary bar (claimed, verified, hold amount) and a drill-down panel
- Hold or approve buttons, with an optional note
- A Bedrock-written evidence summary for each flagged drain
- **One live upload moment** for the video: upload a new photo or slip and watch it get verified

### Out of scope (do not build)
- Login or auth (hard-code one engineer user)
- A contractor-side portal or mobile app
- Multiple wards, cities or bills
- Real-time GPS streaming
- Training any ML model (no SageMaker)
- Step Functions, queues or microservices (one ingestion Lambda and one API Lambda is enough)

---

## 2. Team roles (adjust to team size)

| Role | Owns |
|---|---|
| **A: Backend / AWS** | SAM template, ingestion Lambda, Textract, Bedrock, verification rules, API |
| **B: Frontend** | React app, map, summary bar, drill-down panel, hold/approve flow, Amplify hosting |
| **C: Data + demo** | Photo shoot, OSM drain geometry, slip generator, trip generator, seed script, video, writeup |

With only 2 people, A takes the Bedrock and rules work and B takes the frontend plus data generation; both work on the video on Day 3.

---

## 3. Architecture

```
                 ┌──────────────── Seed script (dataset) / Live upload (presigned URL)
                 ▼
            S3 evidence bucket
   (photos/, slips/, traces/, bill.json)
                 │  S3 event
                 ▼
        Lambda: ingest
   ├─ photos → EXIF (GPS, time) + pHash
   │         → Bedrock vision: cleared? silt vs debris?
   ├─ slips  → Textract AnalyzeDocument (Queries)
   │           "net weight?", "vehicle no?", "time in?"
   └─ traces → parse GPS points
                 │
                 ▼
            DynamoDB (single table)
                 ▲
                 │
   API Gateway (HTTP API) → Lambda: api
   ├─ POST /verify/{billId}   → runs 10 rules, writes verdicts
   ├─ GET  /bill/{billId}     → drains + summary
   ├─ GET  /drain/{drainId}   → trips, evidence, verdicts
   ├─ POST /drain/{id}/summary→ Bedrock text: 2-line evidence summary
   ├─ POST /decision          → hold / approve + note
   └─ POST /upload-url        → presigned S3 URL (live demo)
                 ▲
                 │
   React + MapLibre (Amazon Location map tiles) on Amplify Hosting
```

**Key design choice:** the heavy AI work (Textract, Bedrock vision) runs **at ingestion**, triggered by S3 events. Clicking "Run Verification" runs only the fast rules, so it finishes in seconds, never hits the API Gateway timeout, and is safe to record on camera. Every result is still produced live by AWS.

### AWS services and why each one is there

| Service | Job in SiltProof | Weight in the demo |
|---|---|---|
| **Amazon S3** | Stores all evidence (photos, slips, GPS traces, bill) | Evidence store |
| **AWS Lambda** | `ingest` (S3-triggered extraction) and `api` (rules + endpoints) | Serverless backbone |
| **Amazon Textract** | Reads weighbridge slips using Queries | **Core AI #1** |
| **Amazon Bedrock** (Claude vision model) | Before/after photo check (cleared? silt or debris?) and the evidence summary | **Core AI #2** |
| **Amazon Location Service** | Map tiles for the UI, and route calculation to generate realistic dumper traces | Map and routes |
| **Amazon DynamoDB** | Drains, trips, extracted facts, verdicts, decisions | State |
| **Amazon API Gateway** | HTTP API for the frontend | Glue |
| **AWS Amplify Hosting** | Hosts the React app | Delivery |
| **AWS SAM** | Infrastructure as code, so you can redeploy in minutes | Execution |
| **AWS Budgets / CloudWatch** | Billing alarm, Lambda logs | Safety |

**Geofence checks** (point inside a drain or dump-site polygon) run in Lambda with `shapely` on GeoJSON polygons. That is simpler and more reliable than Location's asynchronous geofence evaluation for this MVP. If time allows, also load the polygons into an Amazon Location geofence collection so they appear in the console for the architecture slide. This is optional.

**Region:** pick **one** region where your chosen Bedrock model, Textract and Amazon Location are all available. Check this in hour 1. Try `ap-south-1` first and fall back to `us-east-1`.

---

## 4. Data model (single DynamoDB table)

Table `siltproof` with partition key `pk` and sort key `sk`.

| pk | sk | Key attributes |
|---|---|---|
| `BILL#B1` | `META` | contractor, ward, rate per tonne, claimed tonnes, claimed ₹, status |
| `BILL#B1` | `DRAIN#14` | name, length, width, depth, geofence (GeoJSON), claimed tonnes, verdict, decision, note |
| `BILL#B1` | `TRIP#14#003` | vehicle no., claimed tonnes, trace key, slip key, photo keys, verdict, failed rules[] |
| `EVID#<s3key>` | `PHOTO` | lat, lon, timestamp, pHash, bedrock {cleared, load_type, confidence, notes} |
| `EVID#<s3key>` | `SLIP` | ticket no., vehicle no., gross, tare, net, time in, time out, Textract confidence |
| `VEHICLE#<no>` | `META` | capacity in tonnes (a simulated stand-in for the Vahan registry) |
| `DUMPSITE#D1` | `META` | approved dump-site geofence |

---

## 5. Verification rules (deterministic, in `api` Lambda)

| # | Rule | Severity |
|---|---|---|
| R1 | Photo GPS lies inside the drain geofence (30 m buffer) | Hard |
| R2 | Photo timestamp falls inside the bill's work window | Hard |
| R3 | Photo is not a duplicate (pHash Hamming distance above threshold) of any other photo in this bill or earlier bills | Hard |
| R4 | Bedrock: the after-photo shows a cleared drain, and the load is silt, not construction debris | Hard if debris, Soft if unclear |
| R5 | The GPS trace enters the approved dump-site geofence | Hard |
| R6 | The time gap between consecutive trips of one truck allows the distance at ≤ 40 km/h | Hard |
| R7 | Slip net weight ≤ vehicle capacity | Hard |
| R8 | Slip time-in is within ±10 min of the GPS arrival at the dump site | Hard |
| R9 | Slip vehicle number matches the trip's vehicle | Hard |
| R10 | Drain total ≤ length × width × depth × silt density × 1.2 | Soft |

Extra soft flag: a GPS gap of more than 3 min goes to **review**, not hold.

**How verdicts roll up**
- Trip: any hard fail means **HOLD**; otherwise any soft fail means **REVIEW**; otherwise **VERIFIED**
- Drain: 🔴 if any trip is held, 🟡 if any is in review, 🟢 otherwise
- Money: verified ₹ = verified tonnes × rate; hold ₹ = held tonnes × rate; review ₹ stays pending until the engineer decides

---

## 6. Simulated dataset spec

**Ward:** a real ward in the city where you shoot the photos. The photos' EXIF GPS must fall inside your drain geofences, so the ward and the photo shoot have to be in the same place. The pitch line: "This fraud has surfaced in Mumbai; we piloted the check on a ward in our city."

| Item | Source | Real or simulated |
|---|---|---|
| Drain lines (18 sections) | OpenStreetMap (Overpass: `waterway=drain|canal|ditch`) buffered to polygons | **Real** geometry |
| Approved dump site | A real, known landfill or dumping ground near the ward | **Real** location |
| Drain photos (15–20) | Shot by the team with **location ON** | **Real** GPS and time; the before/after pairing is simulated |
| Load photos | A silt/mud heap and a construction-debris pile, shot by the team | Real photos |
| Weighbridge slips (about 40) | Python generator with a fictional weighbridge name and a "SAMPLE DATA" mark; 8–10 printed and phone-photographed | Simulated |
| Dumper GPS traces (about 120) | Amazon Location route calculation, drain to dump site, one point every 30 s, plus fraud variants | Simulated (plus **1 real** GPX recording) |
| Bill | 18 drains, ₹1,800/t (illustrative) | Simulated |

### Planted cases (so the demo tells a story)

| Drain | What is wrong | Rules that catch it | Colour |
|---|---|---|---|
| **14** (hero case) | Truck detours to a vacant plot and never reaches the dump; slip printed 40 min before GPS arrival; after-photo reused from drain 9 | R5, R8, R3 | 🔴 |
| 3 | Load is construction rubble passed off as silt | R4 | 🔴 |
| 11 | Two slips show 14 t net on a 10 t truck | R7 | 🟡/🔴 |
| 16 | Same truck logs two trips 7 min apart and 22 km apart | R6 | 🔴 |
| 6 (honest case) | 4-minute GPS gap in an underpass; everything else is fine | soft flag | 🟡, which the engineer approves |
| 8 | One photo taken outside the drain geofence | R1 | 🟡 |
| All others | Clean | none | 🟢 |

Tune the seed so the summary reads roughly **Claimed 1,240 t · Verified 870 t · Hold ₹6.66 lakh**.

### Photo handling gotchas
- **Do not send photos over WhatsApp**, because it strips the EXIF data. Move originals by cable, Google Drive or AirDrop.
- Blur faces and real number plates before upload.
- Make one exact duplicate and one edited duplicate (cropped, brightened) to show that pHash catches edited reuse.

---

## 7. Day-by-day plan

### Tonight (7 Oct): setup only, no project code
- [ ] Confirm the team, roles, repo name and comms channel
- [ ] AWS account ready; **request Bedrock model access** (approval can take time)
- [ ] Set up an AWS Budget alarm
- [ ] Re-read the hackathon rules: submission deadline, video length, where the video goes, what to submit
- [ ] Charge phones; plan the photo route (3–5 drains near the chosen ward and a debris pile)

---

### Day 1 (8 Oct): Foundation and evidence pipeline
**Goal: all evidence is in S3, AWS has extracted facts into DynamoDB, and a map shows the drains.**

**Morning (first 4 hours)**
- [ ] **A:** confirm the region works for Bedrock + Textract + Location; SAM skeleton (S3 bucket, DynamoDB table, two Lambdas, HTTP API); deploy "hello"
- [ ] **B:** React + Vite + MapLibre app with Amazon Location map tiles (API key); deploy to Amplify Hosting; show the ward on the map
- [ ] **C:** **photo shoot** (location ON); pull drain lines from OSM and turn them into 30 m polygons; choose the dump site

**Afternoon**
- [ ] **A:** `ingest` Lambda for **slips**, using Textract `AnalyzeDocument` with QUERIES (net weight, gross, tare, vehicle no., time in, time out, ticket no.), writing to DynamoDB
- [ ] **A:** `ingest` for **photos**: EXIF GPS and time, pHash (`imagehash` + Pillow via a Lambda layer or container image)
- [ ] **C:** slip generator (Pillow or HTML-to-image); print 8–10, photograph them, augment the rest
- [ ] **C:** trip generator using Amazon Location route calculation, densified to 30 s points with timestamps, plus the fraud variants from §6
- [ ] **B:** render drain polygons and the dump site from a static GeoJSON

**Evening**
- [ ] **A:** Bedrock vision call for photos with a strict JSON output: `{cleared: bool, load_type: silt|debris|unclear, confidence, notes}`
- [ ] **C:** `seed.py` uploads the whole dataset to S3 and writes bill, drain, trip and vehicle records
- [ ] **Everyone:** run the seed and check DynamoDB has extracted facts for every photo and slip

**✅ Day 1 exit check:** every photo and slip in S3 has extracted facts in DynamoDB, and the map shows the 18 drains.

---

### Day 2 (9 Oct): Verification and the decision screen
**Goal: the full demo flow works end to end. Feature freeze tonight.**

**Morning**
- [ ] **A:** rules R1–R10 in the `api` Lambda; `POST /verify/{billId}` writes trip and drain verdicts and the money summary
- [ ] **A:** `GET /bill`, `GET /drain/{id}`
- [ ] **B:** the main screen: drains coloured by verdict, the **summary bar** (claimed, verified, hold), and the **Run Verification** button with a short "verifying…" animation
- [ ] **C:** spot-check the planted cases from §6 and tune the seed until the totals read well

**Afternoon**
- [ ] **B:** drill-down panel for a drain:
  - map with the **claimed route vs actual GPS route** in two colours
  - before/after photo pair with the Bedrock verdict
  - slip image with its extracted fields, and the conflicting field highlighted
  - list of failed rules in plain English
- [ ] **A:** `POST /drain/{id}/summary`, a Bedrock text call that turns the failed-rule JSON into a 2-line evidence summary (called on click and cached)
- [ ] **A + B:** `POST /decision` for hold or approve with a note; the summary bar updates

**Evening**
- [ ] Full run-through as the engineer: open bill → Run Verification → drain 14 → hold → drain 6 → approve with note
- [ ] Fix whatever breaks during the run-through
- [ ] **C:** draft the video script (§10) and the architecture slide
- [ ] **🧊 FEATURE FREEZE.** Day 3 is only for polish, the live upload, the video and the writeup.

**✅ Day 2 exit check:** a stranger could click through the full story without help.

---

### Day 3 (10 Oct): Polish, live moment, video, submit
**Goal: submit early, with buffer hours.**

**Morning**
- [ ] **A + B:** live upload, using `POST /upload-url` (presigned S3 URL) and a small upload button. The S3 event triggers ingestion, the screen polls, and the new evidence appears verified or flagged within seconds.
- [ ] **A:** `reset.py` restores the clean demo state in one command, so you can re-record any number of times
- [ ] **B:** UI polish: rupee formatting, loading states, empty states, consistent colours, and a small "Simulated data" label on screen
- [ ] **C:** architecture diagram for the video showing every AWS service with its logo

**Afternoon**
- [ ] Record the demo (screen recording) in 2–3 takes; record the voiceover separately
- [ ] Edit to **3 minutes or less**
- [ ] Writeup and README (§11)
- [ ] **Submit at least 3 hours before the deadline**

---

## 8. Cut list (if behind, drop in this order)

1. The real GPX trace (keep generated traces only)
2. The edited-duplicate pHash case (keep the exact duplicate)
3. Notes on approve or hold (keep the buttons)
4. Rule R10 (drain plausibility)
5. Live upload in the app; instead, upload through the S3 console on camera and refresh

**Never cut:** the coloured map, the summary bar, the drain 14 drill-down with its two routes, the Bedrock and Textract outputs on screen, and the architecture shot in the video.

---

## 9. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Bedrock model access is delayed | Request it tonight; keep a second Claude model enabled as a fallback |
| Textract misreads crumpled slips | Use crisp slips for the hero case and keep crumpled ones as "it still works" extras; show the confidence score |
| Photos lose EXIF data | Never send them through WhatsApp; check EXIF data on Day 1 morning |
| Pillow or imagehash won't package for Lambda | Use a Lambda container image or a prebuilt layer; or compute pHash in `seed.py` and store it (last resort) |
| Bedrock throttling while seeding about 40 photos | Limit the ingest Lambda's reserved concurrency to 2–3 and retry with backoff |
| API timeout during verification | AI runs at ingestion, so verification is fast rules only |
| Cost overrun | Budget alarm; run Bedrock and Textract only on seed and live upload, never on page load |
| Demo breaks while recording | `reset.py` plus cached results; record in short takes |

---

## 10. Video script (3:00)

| Time | Screen | Voiceover |
|---|---|---|
| 0:00–0:30 | Headline with source: ₹65.54 crore alleged Mithi desilting fraud | "Cities pay contractors per tonne of silt. Police allege ₹65 crore was paid for silt that was never removed. Engineers have no way to check." |
| 0:30–1:00 | Bill inbox: 18 drains, 1,240 t, ₹22.32 lakh → **Run Verification** | "This is a ward engineer two weeks before the monsoon, with one bill to approve." |
| 1:00–1:30 | Map turns green, amber and red; summary bar: **Hold ₹6.66 lakh** | "In seconds, every tonne is checked against GPS, photos and weighbridge slips." |
| 1:30–2:15 | Drain 14: two routes, slip time conflict, reused photo, Bedrock summary → **HOLD** | "The truck never reached the dump site. The slip was printed before it arrived. The photo is from another drain." |
| 2:15–2:30 | Drain 6: GPS gap → engineer approves with a note | "Not every flag is fraud. The engineer decides; we show the evidence." |
| 2:30–2:45 | Live upload: a new slip is read and verified | "Every check runs live on AWS." |
| 2:45–3:00 | Architecture diagram with AWS logos | "S3, Lambda, Textract, Bedrock, Amazon Location, DynamoDB, API Gateway and Amplify." |

The rules require the video to **show where AWS is used**, so the architecture shot is mandatory, and it helps to show Textract and Bedrock output on screen during the demo too.

---

## 11. Submission checklist

- [ ] Video of 3 minutes or less, with the AWS architecture shown
- [ ] Writeup covering:
  - [ ] **the trigger**, with links: Outlook India on the Mithi case; The Week (PTI) on the probe and on the minister calling desilting unreliable
  - [ ] the user (ward storm-water engineer) and the decision (pay or hold per drain)
  - [ ] how it works (ingestion → rules → decision screen)
  - [ ] the AWS services and what each one does
  - [ ] **an honest data statement**: "Contractor bills aren't public. Our photos are real, with real GPS and timestamps; slips and traces are simulated; every check runs live on AWS."
  - [ ] next steps: integrate with municipal billing systems, live GPS from dumper trackers, a public view for ward councillors
- [ ] Public repo with a README (setup, `seed.py`, `reset.py`, architecture diagram)
- [ ] Live app link (Amplify)
- [ ] Re-check: no code written before 8 Oct kickoff

---

## 12. How this maps to judging

| Criterion | What earns it |
|---|---|
| **Impact** | A sourced ₹65 crore case, a real user, and money visibly held |
| **Built on AWS** | Textract and Bedrock do real work; serverless backbone; Amazon Location maps and routes |
| **Design** | One clear screen, colour-coded map, claimed-vs-actual routes |
| **Execution** | A live pipeline, a live upload moment, IaC, one-command reset |
| **Demo video** | A story with a hero case and an honest case, in under 3 minutes |