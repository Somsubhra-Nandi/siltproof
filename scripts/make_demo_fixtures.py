#!/usr/bin/env python3
"""Write a snapshot of the API's responses for the frontend to run on offline.

    python scripts/make_demo_fixtures.py

Runs the whole pipeline in memory - generators, a moto S3 and DynamoDB, the
ingest Lambda with MOCK_AWS=1, then POST /verify - and saves what the API
returned into frontend/public/data/demo/.

Two uses:

* the decision screen is clickable with no AWS account, which is the only way
  to look at it while the account is still blocked;
* it is a fallback during recording if the deployed stack is unreachable.

Touches no AWS and costs nothing. The frontend uses the snapshot whenever
VITE_API_BASE_URL is empty.
"""

import json
import os
import pathlib
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "backend"), str(REPO / "data")]

# Drains whose images are copied into the snapshot, so the offline demo can
# show them: all 18, so every case file has its slips and photos. The
# originals of any photo these drains reuse come along too. Presigned links
# never go in here.
OFFLINE_IMAGE_DRAINS = tuple(str(drain) for drain in range(1, 19))

BUCKET = "siltproof-demo-evidence"
TABLE = "siltproof-demo"
OUT = REPO / "frontend" / "public" / "data" / "demo"


def configure_env(out_dir):
    os.environ.update(
        AWS_ACCESS_KEY_ID="demo",
        AWS_SECRET_ACCESS_KEY="demo",
        AWS_SESSION_TOKEN="demo",
        AWS_DEFAULT_REGION="ap-south-1",
        AWS_REGION="ap-south-1",
        MOCK_AWS="1",
        TABLE_NAME=TABLE,
        EVIDENCE_BUCKET=BUCKET,
        BILL_ID="B1",
        MOCK_MANIFEST_PATH=str(out_dir / "mock_manifest.json"),
    )


def call(api_app, route_key, *, path=None, body=None, query=None):
    event = {"routeKey": route_key}
    if path:
        event["pathParameters"] = path
    if body is not None:
        event["body"] = json.dumps(body)
    if query:
        event["queryStringParameters"] = query

    result = api_app.lambda_handler(event, None)
    return result["statusCode"], json.loads(result["body"])


def localise_images(responses, evidence_root):
    """Swap presigned links for local copies, or null, in the drain responses.

    The live API signs a 5-minute link for every image. A snapshot must not
    keep those: they expire, and they point at a bucket. The drains'
    images (every drain in OFFLINE_IMAGE_DRAINS), and the originals their
    reused photos copy, are copied next to the snapshot instead and linked by
    path; anything else is null.
    """
    keep = set()
    for drain_id in OFFLINE_IMAGE_DRAINS:
        drain = responses.get(drain_id) or {}
        keep.update(photo["s3Key"] for photo in drain.get("photos", []))
        keep.update(trip["slipKey"] for trip in drain.get("trips", []) if trip.get("slipKey"))
        for trip in drain.get("trips", []):
            for item in trip.get("findings", []):
                if item["rule"] == "R3" and item["evidence"].get("original"):
                    keep.add(item["evidence"]["original"])

    def local(key):
        source = evidence_root / key
        if key not in keep or not source.exists():
            return None
        target = OUT / "evidence" / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return f"/data/demo/evidence/{key}"

    for drain in responses.values():
        drain["evidenceUrlExpiresInSeconds"] = None
        for photo in drain.get("photos", []):
            photo["imageUrl"] = local(photo["s3Key"])
        for trip in drain.get("trips", []):
            trip["slipImageUrl"] = local(trip["slipKey"]) if trip.get("slipKey") else None

    return len(list((OUT / "evidence").rglob("*.*"))) if (OUT / "evidence").exists() else 0


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--center", default="19.0760,72.8777",
                        help="ward centre for the synthetic drains")
    parser.add_argument("--ward", default="Ward",
                        help="ward name written into the snapshot")
    parser.add_argument("--keep", action="store_true",
                        help="reuse data/out instead of generating into a temp dir")
    args = parser.parse_args(argv)

    import boto3
    from moto import mock_aws

    import dataset as ds

    work = ds.OUT if args.keep else pathlib.Path(
        __import__("tempfile").mkdtemp(prefix="siltproof-demo-")
    )
    ds.OUT = work
    configure_env(work)

    import gen_photos
    import gen_slips
    import gen_trips
    import osm_drains
    import seed as seed_script

    print(f"working in {work}")

    with mock_aws():
        s3 = boto3.client("s3", region_name="ap-south-1")
        s3.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": "ap-south-1"},
        )
        boto3.client("dynamodb", region_name="ap-south-1").create_table(
            TableName=TABLE,
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        from common import awsclients

        awsclients.reset_cache()

        if not (work / "drains.geojson").exists():
            osm_drains.main(["--synthetic", "--center", args.center])
        if not (work / "trips.json").exists():
            gen_trips.main([])
        # Every trip needs its slip: without one, rules R7-R9 cannot run and
        # the trip correctly falls to review, which would make the snapshot
        # show numbers the demo never produces.
        gen_slips.main([])
        gen_photos.main([])

        seed_script.main(
            ["--live", "--yes", "--bucket", BUCKET, "--table", TABLE,
             "--ward", args.ward]
        )

        import ingest.app as ingest_app

        keys = []
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET):
            keys.extend(obj["Key"] for obj in page.get("Contents", []) or [])

        print(f"ingesting {len(keys)} objects...")
        for start in range(0, len(keys), 25):
            ingest_app.lambda_handler(
                {
                    "Records": [
                        {"s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}
                        for key in keys[start:start + 25]
                    ]
                },
                None,
            )

        import api.app as api_app

        status, _ = call(api_app, "POST /verify/{billId}", path={"billId": "B1"})
        if status != 200:
            print(f"verification failed: {status}")
            return 1

        status, bill = call(api_app, "GET /bill/{billId}", path={"billId": "B1"})
        if status != 200:
            print(f"could not read the bill: {status}")
            return 1

        if OUT.exists():
            shutil.rmtree(OUT)
        OUT.mkdir(parents=True)

        (OUT / "bill.json").write_text(json.dumps(bill, indent=1) + "\n")
        written = 1
        responses = {}

        for row in bill["drains"]:
            drain_id = row["drainId"]
            status, drain = call(
                api_app, "GET /drain/{drainId}", path={"drainId": drain_id}
            )
            if status != 200:
                print(f"  drain {drain_id}: {status}")
                continue

            # Cache the evidence summary for the flagged drains, so the
            # "explain in two lines" button has something to show offline.
            if drain["failedRules"] or any(
                trip["hardFails"] or trip["softFails"] for trip in drain["trips"]
            ):
                _, summary = call(
                    api_app, "POST /drain/{drainId}/summary",
                    path={"drainId": drain_id}, body={},
                )
                drain["summary"] = summary.get("summary")

            responses[drain_id] = drain

        copied = localise_images(responses, work / "evidence")
        for drain_id, drain in responses.items():
            text = json.dumps(drain, indent=1) + "\n"
            if "X-Amz-" in text:
                raise SystemExit(f"drain {drain_id}: a presigned link reached the snapshot")
            (OUT / f"drain-{drain_id}.json").write_text(text)
            written += 1

        # The map reads the same geometry the snapshot was built from.
        public = REPO / "frontend" / "public" / "data"
        for name in ("drains.geojson", "dumpsite.geojson"):
            geometry = json.loads((work / name).read_text())
            for feature in geometry["features"]:
                feature["properties"].pop("centreline", None)
            (public / name).write_text(json.dumps(geometry, indent=1) + "\n")

    size = sum(path.stat().st_size for path in OUT.rglob("*.json"))
    summary = bill["summary"]

    print(f"\nwrote {written} files to {OUT.relative_to(REPO)} ({size / 1024:.0f} KB)")
    print(
        f"claimed {summary['claimedTonnes']:.0f} t · verified {summary['verifiedTonnes']:.0f} t"
        f" · review {summary['reviewTonnes']:.0f} t · hold Rs {summary['heldRupees'] / 100000:.2f} lakh"
    )
    print(f"images {copied} copied for the offline demo (drains {', '.join(OFFLINE_IMAGE_DRAINS)})")
    print("\nStart the app with no VITE_API_BASE_URL and it will use this snapshot.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
