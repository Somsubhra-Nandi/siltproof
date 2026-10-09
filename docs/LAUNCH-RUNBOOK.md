# Launch runbook: both pages online (10 Oct 2026)

Every step below changes AWS or costs money and needs the team's go-ahead.
Profile `siltproof`, region `ap-south-1`, stack `siltproof`.

## State on the night of 9 Oct

- The trial change set was executed at 17:12 UTC: trial routes, TTL,
  lifecycle, throttles, B1 operator routes off, trials closed (no invite code).
- **The live API answers 500 on every route** since then: the API Lambda
  imported `common.photo` (Pillow/imagehash, ingest layer only) through
  `trial.process`. Fixed in `021c96f`, with `tests/test_api_packaging.py`.
  Nothing public used the API yet. The ingest function is healthy.
- Prepared, not executed: change set
  `arn:aws:cloudformation:ap-south-1:967226344298:changeSet/samcli-deploy1791569716/48cd4633-49ea-4a23-a01e-51a7ec6de15a`,
  code only (both functions; the artifact matches `backend/` at the branch
  head), parameters unchanged: Nova Pro, `B1OperatorRoutes=disabled`, invite
  code empty. It also makes `POST /verify` and `POST /decision` read-only.

## State on the morning of 10 Oct (updated)

- API fixed: corrective change set executed 18:22 UTC 9 Oct; `/health` 200,
  trials closed (503), `/verify` and `/decision` 403 READ_ONLY.
- **Public site:** https://main.d1o7ayjtge649w.amplifyapp.com (Amplify app
  `d1o7ayjtge649w`, branch `main`, manual deploys). Job 1: offline basemap.
  Job 2: Amazon Location Monochrome Light. **Job 3 (current, `d6b254b`):**
  job 2 plus the trial-map fixes (jump to the first evidence, keep the
  Amazon map through tile errors). Backend change set `provenance-fix`
  (code only, all parameters kept) executed 20:06 UTC 9 Oct.
- Location key `siltproof-maps-browser`: `geo-maps:*` on
  `arn:aws:geo-maps:ap-south-1::provider/default`, referrers the Amplify URL
  and `http://localhost:4173/*`, expires 2026-11-30. Tiles refuse other
  referrers (403); the style descriptor (no tiles) is served to anyone.
- Rollback for the map: redeploy job 1's bundle (kept outside the repo) or
  rebuild without `VITE_LOCATION_API_KEY`; the app also falls back on its
  own if the key is refused.

- **Trials open (19:28 UTC 9 Oct, 00:58 IST 10 Oct):** parameter-only change set
  `trial-invite-code` set `TrialInviteCode` (32 URL-safe characters,
  192 bits). The code is in `%USERPROFILE%/.siltproof/trial-invite-code.txt`
  on the laptop that set it (readable by that Windows account only); it is
  not in git, the bundle, Amplify or any log. Without it `POST /trials` is
  403 INVITE_REQUIRED.
- **Live smoke test passed** (`scripts/trial_live_smoke.py --inspect-aws`):
  one synthetic 5.2 MB JPEG and one generated slip; 1 Nova Pro call on the
  1568 px processing copy, 1 Textract call, all checks green, trial deleted.

## 1. Fix the API (code-only change set) - done

```bash
CS=arn:aws:cloudformation:ap-south-1:967226344298:changeSet/samcli-deploy1791569716/48cd4633-49ea-4a23-a01e-51a7ec6de15a
aws cloudformation describe-change-set --profile siltproof --region ap-south-1 --change-set-name $CS --query '[Status,ExecutionStatus]'
aws cloudformation execute-change-set --profile siltproof --region ap-south-1 --change-set-name $CS
aws cloudformation wait stack-update-complete --profile siltproof --region ap-south-1 --stack-name siltproof
API=https://j0scrvpupi.execute-api.ap-south-1.amazonaws.com
curl -s $API/health                                   # 200
curl -s -X POST $API/trials -d '{}'                   # 503 TRIALS_DISABLED
curl -s -X POST $API/decision -d '{"drainId":"1","decision":"HOLD"}'   # 403 READ_ONLY
```

## 2. Amplify app (manual deploy, no GitHub connection needed)

```bash
aws amplify create-app --profile siltproof --region ap-south-1 --name siltproof --platform WEB
aws amplify create-branch --profile siltproof --region ap-south-1 --app-id <appId> --branch-name main
# site: https://main.<appId>.amplifyapp.com
```

## 3. Location key (needs the Amplify URL as a referrer)

See `docs/amazon-location.md`, "Creating the API key". Then build with the
key and run `scripts/check-location-live.mjs --live` against `vite preview`
on port 4173 (billable, a few dozen tile requests). Look at the screenshots.

## 4. Build and deploy the frontend

```bash
cd frontend
VITE_API_BASE_URL=$API VITE_BILL_SOURCE=snapshot VITE_AWS_REGION=ap-south-1 \
VITE_LOCATION_API_KEY=<key> npm run build      # never an invite code here
(cd dist && zip -r ../site.zip .)              # or: Compress-Archive dist\* site.zip
aws amplify create-deployment --profile siltproof --region ap-south-1 --app-id <appId> --branch-name main
curl -T site.zip "<zipUploadUrl>"
aws amplify start-deployment --profile siltproof --region ap-south-1 --app-id <appId> --branch-name main --job-id <jobId>
```

Check `https://main.<appId>.amplifyapp.com/` (the 18-drain investigation,
labelled simulated, decisions in the browser only) and `/trial.html`
("Trials are not open" until step 5).

Without the key, leave `VITE_LOCATION_API_KEY` unset: the bundled offline
basemap is used and the page still works.

## 5. Open trials (when the team decides)

```bash
CODE=$(python -c "import secrets;print(secrets.token_urlsafe(18))")   # keep it off chat logs
sam deploy --template-file .aws-sam/build/template.yaml --stack-name siltproof \
  --region ap-south-1 --capabilities CAPABILITY_IAM --resolve-s3 --no-execute-changeset \
  --parameter-overrides BedrockModelId=apac.amazon.nova-pro-v1:0 \
  BedrockVisionModelId=apac.amazon.nova-pro-v1:0 IngestReservedConcurrency=0 BillId=B1 \
  B1OperatorRoutes=disabled TrialDailyTrials=60 TrialDailyBedrockCalls=200 \
  TrialDailyTextractCalls=100 TrialInviteCode=$CODE
```

SAM prints parameter overrides; run it in your own terminal. Review the
change set (it should change only ApiFunction's environment) and execute it.
Then the smoke test, one Nova Pro and one Textract call (about $0.02):

```bash
TRIAL_INVITE_CODE=$CODE python scripts/trial_live_smoke.py --api $API --photo <one.jpg> --live
```

## 6. Optional: seed B1 and serve the investigation from the API

Dry run first; 117 Textract pages, 0 Bedrock calls (about $1.75):

```bash
python data/seed.py --simulated-vision                    # plan
python data/seed.py --simulated-vision --live --bucket siltproof-evidence-967226344298
python data/check_seed.py --live                          # read-only comparison
python data/check_seed.py --live --verify                 # rules, only if all match
```

Only if the totals match the ground truth (1,240 / 805 / 65 / 370 t), rebuild
the frontend with `VITE_BILL_SOURCE=api` and redeploy step 4.

## Rollback

- API: execute nothing further; or redeploy the previous branch head the
  same way (CloudFormation rolls back a failed update on its own).
- Frontend: `aws amplify start-deployment` with the previous zip, or delete
  the Amplify app.
- Location key: `aws location delete-key --key-name siltproof-maps-browser --force-delete`.
