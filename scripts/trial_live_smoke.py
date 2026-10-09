#!/usr/bin/env python3
"""Controlled smoke test of the judge trial against a deployed stack.

One trial, one JPEG photo and one weighbridge slip, through the real API:
presigned POST to S3, the async ingest invoke, one Amazon Nova Pro call and
one Amazon Textract call, then analysis and delete-now.

Dry run by default: prints the plan and the billable calls, and stops.

    python scripts/trial_live_smoke.py --api <ApiUrl> --photo IMG.jpg
    python scripts/trial_live_smoke.py --api <ApiUrl> --photo IMG.jpg --live

The invite code is read from --invite-file, $TRIAL_INVITE_CODE, or a prompt
without echo; it is never taken on the command line, printed or saved.

--inspect-aws also reads the deployment directly with your AWS credentials
(read-only): the daily quota counters before and after, the trial's S3 object
names and DynamoDB keys, token isolation, and that delete-now left nothing. Against the
offline dev server (scripts/trial_dev_server.py) no code is needed, and the
script reports the run as MOCK rather than as a pass.

The photo is uploaded byte for byte, so its EXIF is preserved; the script
checks the stored SHA-256 against the local file. Responses are saved to
data/out/trial_smoke/ (git-ignored) with preview links stripped.
"""

import argparse
import getpass
import hashlib
import json
import mimetypes
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_SLIP = REPO / "data" / "out" / "evidence" / "slips" / "B1" / "1-001.png"
OUT = REPO / "data" / "out" / "trial_smoke"

# Rough list prices, for the plan only.
COST_TEXTRACT_QUERIES_PAGE = 0.015
COST_NOVA_PRO_PHOTO = 0.004


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def content_type(path):
    guessed = mimetypes.guess_type(str(path))[0] or ""
    return "image/jpeg" if guessed == "image/jpg" else guessed


class Api:
    def __init__(self, base, requests):
        self.base = base.rstrip("/")
        self.requests = requests
        self.token = None

    def call(self, method, path, body=None, expect=(200, 201, 202)):
        headers = {"content-type": "application/json"}
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        response = self.requests.request(method, self.base + path, headers=headers,
                                         data=json.dumps(body) if body is not None else None,
                                         timeout=30)
        payload = response.json() if response.content else {}
        if response.status_code not in expect:
            raise SystemExit(f"{method} {path} -> {response.status_code} "
                             f"{payload.get('code')}: {payload.get('error')}")
        return payload


def redact(value):
    """Drop presigned links and the token before anything is saved."""
    if isinstance(value, dict):
        # "fields" is dropped only beside a "url" (a presigned POST form), so
        # Textract's own slip fields are kept.
        return {k: redact(v) for k, v in value.items()
                if k not in ("previewUrl", "url", "accessToken")
                and not (k == "fields" and "url" in value)}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def upload(api, requests, trial_id, path, group, role=None):
    body = {"group": group, "filename": pathlib.Path(path).name,
            "contentType": content_type(path), "sizeBytes": pathlib.Path(path).stat().st_size}
    if role:
        body["role"] = role
    ticket = api.call("POST", f"/trials/{trial_id}/upload-url", body)
    with open(path, "rb") as handle:
        stored = requests.post(ticket["upload"]["url"], data=ticket["upload"]["fields"],
                               files={"file": (body["filename"], handle, body["contentType"])},
                               timeout=120)
    if stored.status_code not in (200, 201, 204):
        raise SystemExit(f"S3 refused the {group} upload: {stored.status_code} {stored.text[:200]}")
    evidence_id = ticket["evidenceId"]
    api.call("POST", f"/trials/{trial_id}/evidence/{evidence_id}/complete", {})
    return evidence_id


def wait_ready(api, trial_id, ids, timeout_s):
    deadline = time.time() + timeout_s
    while True:
        trial = api.call("GET", f"/trials/{trial_id}")
        items = {item["evidenceId"]: item for item in trial["evidence"]}
        if all(items[i]["state"] in ("READY", "FAILED", "REJECTED") for i in ids):
            return trial, items
        if time.time() > deadline:
            raise SystemExit("timed out waiting for processing: "
                             + ", ".join(f"{i}={items[i]['state']}" for i in ids))
        time.sleep(3)


class AwsInspector:
    """Read-only checks against the deployment, with the caller's credentials."""

    def __init__(self):
        import boto3

        self.region = os.environ.get("AWS_REGION", "ap-south-1")
        self.bucket = os.environ["EVIDENCE_BUCKET"]
        self.table = boto3.resource("dynamodb", region_name=self.region).Table(
            os.environ.get("TABLE_NAME", "siltproof"))
        self.s3 = boto3.client("s3", region_name=self.region)

    def quotas(self):
        import datetime

        day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        out = {}
        for kind in ("BEDROCK", "TEXTRACT", "TRIALS"):
            item = self.table.get_item(Key={"pk": f"QUOTA#{day}", "sk": kind}).get("Item") or {}
            out[kind] = int(item.get("count") or 0)
        return out

    def keys(self, trial_id):
        listing = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=f"trials/{trial_id}/")
        return sorted(obj["Key"].split(f"trials/{trial_id}/", 1)[1] for obj in listing.get("Contents", []))

    def items(self, trial_id):
        from boto3.dynamodb.conditions import Key

        found = self.table.query(KeyConditionExpression=Key("pk").eq(f"TRIAL#{trial_id}"))
        return sorted(item["sk"] for item in found.get("Items", []))

    def during(self, trial_id, api, requests, vision, before):
        after = self.quotas()
        keys = self.keys(trial_id)
        sks = self.items(trial_id)
        originals = [k for k in keys if k.startswith("originals/")]
        copies = [k for k in keys if k.startswith("processing/")]
        print(f"\n  s3:       {len(originals)} originals, {len(copies)} processing copies under trials/<id>/")
        print(f"  dynamodb: {', '.join(sk.split('#')[0] for sk in sks)}")
        print(f"  quotas:   before {before}, after {after}")
        url = f"{api.base}/trials/{trial_id}"
        no_token = requests.get(url, timeout=30).status_code
        wrong = requests.get(url, headers={"authorization": "Bearer " + "x" * 43}, timeout=30).status_code
        return [
            ("S3: both originals stored under the trial's own prefix", len(originals) == 2),
            ("S3: processing copy only when Nova got one",
             (len(copies) == 1) == (vision.get("input") == "processing_copy")),
            ("DynamoDB: trial, two evidence items, latest result",
             sks.count("META") == 1 and sum(sk.startswith("EVID#") for sk in sks) == 2
             and "RESULT#LATEST" in sks),
            ("daily quota: exactly +1 Bedrock and +1 Textract",
             after["BEDROCK"] - before["BEDROCK"] == 1 and after["TEXTRACT"] - before["TEXTRACT"] == 1),
            (f"isolation: no token {no_token}, wrong token {wrong} (both 404)", no_token == wrong == 404),
        ]

    def leftovers(self, trial_id):
        return {"s3": len(self.keys(trial_id)), "dynamodb": len(self.items(trial_id))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", required=True, help="ApiUrl from the stack outputs")
    parser.add_argument("--photo", required=True, help="one JPEG with its original EXIF")
    parser.add_argument("--slip", default=str(DEFAULT_SLIP),
                        help="one slip: JPEG, PNG or single-page PDF (default: a generated slip)")
    parser.add_argument("--live", action="store_true", help="actually call the API. Costs money.")
    parser.add_argument("--keep", action="store_true", help="do not delete the trial at the end")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--invite-file", default=None,
                        help="file holding the invite code (read, never printed)")
    parser.add_argument("--inspect-aws", action="store_true",
                        help="also check S3, DynamoDB and quota counters directly (read-only)")
    args = parser.parse_args(argv)

    photo, slip = pathlib.Path(args.photo), pathlib.Path(args.slip)
    for path, group, allowed in ((photo, "photo", {"image/jpeg"}),
                                 (slip, "slip", {"image/jpeg", "image/png", "application/pdf"})):
        if not path.is_file():
            raise SystemExit(f"no such {group} file: {path}")
        if content_type(path) not in allowed:
            raise SystemExit(f"{path.name}: {group} must be one of {', '.join(sorted(allowed))}")

    size_mb = photo.stat().st_size / 1024 / 1024
    print("SiltProof trial smoke test" + ("" if args.live else " (dry run)"))
    print(f"  api        {args.api}")
    print(f"  photo      {photo.name}, {size_mb:.1f} MB"
          + (" (over 3.5 MB: Nova gets a resized copy)" if size_mb > 3.5 * 1000 * 1000 / 1024 / 1024 else ""))
    print(f"  slip       {slip.name}, {slip.stat().st_size / 1024:.0f} KB")
    print("  billable   1 Amazon Nova Pro call, 1 Textract AnalyzeDocument (QUERIES) page,")
    print(f"             about ${COST_NOVA_PRO_PHOTO + COST_TEXTRACT_QUERIES_PAGE:.3f}, "
          "plus Lambda, API, S3 and DynamoDB requests")
    print("  cleanup    DELETE /trials/{id} at the end" + (" (skipped: --keep)" if args.keep else ""))
    if not args.live:
        print("\nDry run. Nothing was called. Re-run with --live once approved.")
        return 0

    import requests

    api = Api(args.api, requests)
    health = api.call("GET", "/health")
    mock = bool(health.get("mockAws"))
    body = {"label": "live smoke", "kind": "field"}
    if not mock and "localhost" not in args.api and "127.0.0.1" not in args.api:
        if args.invite_file:
            body["inviteCode"] = pathlib.Path(args.invite_file).read_text(encoding="utf-8").strip()
        else:
            body["inviteCode"] = os.environ.get("TRIAL_INVITE_CODE") or getpass.getpass("Invite code: ")
    inspector = AwsInspector() if args.inspect_aws else None
    quota_before = inspector.quotas() if inspector else None
    created = api.call("POST", "/trials", body)
    trial_id, api.token = created["trialId"], created["accessToken"]
    print(f"\ntrial {trial_id} created")

    try:
        photo_id = upload(api, requests, trial_id, photo, "photo", role="current")
        slip_id = upload(api, requests, trial_id, slip, "slip")
        print("uploaded; waiting for processing...")
        trial, items = wait_ready(api, trial_id, [photo_id, slip_id], args.timeout)
        analysis = api.call("POST", f"/trials/{trial_id}/analyze", {})

        p, s = items[photo_id], items[slip_id]
        vision = (p.get("result") or {}).get("vision") or {}
        exif = (p.get("result") or {}).get("exif") or {}
        fields = (s.get("result") or {}).get("fields") or {}
        checks = [
            ("photo READY", p["state"] == "READY"),
            ("photo bytes unchanged (SHA-256)", p.get("sha256") == sha256(photo)),
            ("EXIF read from the original", bool(exif.get("hasTimestamp") or exif.get("hasGps"))),
            ("Nova Pro ran", vision.get("ran") is True),
            ("vision not mocked, model named", vision.get("mocked") is False and bool(vision.get("modelId"))),
            ("slip READY", s["state"] == "READY"),
            ("Textract not mocked", (s.get("result") or {}).get("mocked") is False),
            ("Textract returned fields", any(v for v in fields.values()) if isinstance(fields, dict) else bool(fields)),
            ("one Bedrock and one Textract call counted",
             trial["usage"]["bedrockCalls"] == 1 and trial["usage"]["textractCalls"] == 1),
            ("analysis ran", bool(analysis.get("analysisId"))),
        ]
        if inspector:
            checks += inspector.during(trial_id, api, requests, vision, quota_before)
        print()
        for name, ok in checks:
            print(f"  {'ok  ' if ok else 'FAIL'} {name}")
        print(f"\n  vision: model={vision.get('modelId')} input={vision.get('input')} "
              f"cleared={vision.get('cleared')} load={vision.get('loadType')} "
              f"confidence={vision.get('confidence')}")
        print(f"  exif:   time={exif.get('timestamp')} gps={exif.get('lat')},{exif.get('lon')} "
              f"camera={exif.get('camera')}")
        print(f"  slip:   {json.dumps(fields)[:300]}")
        print(f"  checks: {analysis.get('counts')}")

        OUT.mkdir(parents=True, exist_ok=True)
        saved = OUT / f"{trial_id}.json"
        saved.write_text(json.dumps(redact({"trial": trial, "analysis": analysis}), indent=1,
                                    default=str), encoding="utf-8")
        print(f"\nsaved {saved.relative_to(REPO).as_posix()} (links and token removed)")
        failed = [name for name, ok in checks if not ok]
        if mock:
            print("\nMOCK: the API runs with MOCK_AWS=1, so this was not a live test.")
            return 2
        return 1 if failed else 0
    finally:
        if not args.keep:
            api.call("DELETE", f"/trials/{trial_id}")
            print(f"trial {trial_id} deleted")
            if inspector:
                left = inspector.leftovers(trial_id)
                print(f"  {'ok  ' if not any(left.values()) else 'FAIL'} cleanup: "
                      f"{left['s3']} S3 objects and {left['dynamodb']} DynamoDB items left")


if __name__ == "__main__":
    sys.exit(main())
