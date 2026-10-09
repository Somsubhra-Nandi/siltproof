#!/usr/bin/env python3
"""Controlled smoke test of the judge trial against a deployed stack.

One trial, one JPEG photo and one weighbridge slip, through the real API:
presigned POST to S3, the async ingest invoke, one Amazon Nova Pro call and
one Amazon Textract call, then analysis and delete-now.

Dry run by default: prints the plan and the billable calls, and stops.

    python scripts/trial_live_smoke.py --api <ApiUrl> --photo IMG.jpg
    python scripts/trial_live_smoke.py --api <ApiUrl> --photo IMG.jpg --live

The invite code is read from $TRIAL_INVITE_CODE, or prompted for without
echo; it is never taken on the command line, printed or saved. Against the
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
        return {k: redact(v) for k, v in value.items()
                if k not in ("previewUrl", "url", "fields", "accessToken")}
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
        body["inviteCode"] = os.environ.get("TRIAL_INVITE_CODE") or getpass.getpass("Invite code: ")
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


if __name__ == "__main__":
    sys.exit(main())
