#!/usr/bin/env python3
"""Upload the demo dataset to S3 and write its DynamoDB records.

    python data/seed.py                      # dry run: writes a local dump
    python data/seed.py --live               # the real thing, after confirming

Dry run is the default and touches nothing: it writes every S3 PUT and every
DynamoDB item it *would* make to data/out/seed_dump.json, so the whole shape
can be reviewed and tested offline.

--live prints the call plan and the billable-call estimate first, then uploads.
Each uploaded photo or slip triggers the ingest Lambda, which calls Bedrock or
Textract, so uploads are paced: --concurrency defaults to 3 to match the
ingest function's reserved concurrency (plan section 9).

Real photos replace the generated ones without changing anything else:

    python data/seed.py --photos-dir ~/siltproof-photos \\
                        --photo-map data/photo_map.csv --live

See data/PHOTO_MAPPING.md for the mapping file.
"""

import argparse
import collections
import concurrent.futures
import csv
import mimetypes
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import dataset as ds  # noqa: E402

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}

# Rough list prices, only for the estimate printed before --live runs.
COST_PER_TEXTRACT_QUERIES_PAGE = 0.015
COST_PER_BEDROCK_PHOTO = 0.004


# ----------------------------------------------------------------- evidence
def walk_evidence(root, prefixes=("photos/", "slips/", "traces/")):
    """Every generated file under data/out/evidence, keyed by its S3 key."""
    root = pathlib.Path(root)
    items = []

    if not root.exists():
        return items

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        key = str(path.relative_to(root))
        if not key.startswith(prefixes):
            continue
        items.append({"key": key, "path": path, "size": path.stat().st_size})

    return items


def read_photo_map(path):
    """CSV rows: filename, drainId, role, note."""
    rows = []
    with open(path, newline="", encoding="utf-8") as handle:
        for line in handle:
            if line.strip().startswith("#") or not line.strip():
                continue
            rows.append(line)

    reader = csv.DictReader(rows)
    mapped = []

    for index, row in enumerate(reader, start=1):
        filename = (row.get("filename") or "").strip()
        drain_id = (row.get("drainId") or "").strip()
        role = (row.get("role") or "").strip().lower()

        if not filename or not drain_id or role not in ("before", "after", "load"):
            print(f"  photo_map row {index}: skipped, needs filename/drainId/role - {row}")
            continue

        mapped.append(
            {
                "filename": filename,
                "drainId": drain_id,
                "role": role,
                "note": (row.get("note") or "").strip(),
            }
        )

    return mapped


def real_photo_items(photos_dir, photo_map_path):
    """Map real photo files onto the key convention the ingest Lambda parses."""
    folder = pathlib.Path(photos_dir).expanduser()
    mapped = read_photo_map(photo_map_path)

    counters = collections.Counter()
    items, missing = [], []

    for row in mapped:
        path = folder / row["filename"]
        if not path.exists():
            missing.append(row["filename"])
            continue

        counters[(row["drainId"], row["role"])] += 1
        sequence = counters[(row["drainId"], row["role"])]
        suffix = path.suffix.lower().lstrip(".") or "jpg"

        items.append(
            {
                "key": ds.photo_key(row["drainId"], row["role"], sequence, ext=suffix),
                "path": path,
                "size": path.stat().st_size,
                "note": row["note"],
            }
        )

    return items, missing


# -------------------------------------------------------------------- items
def bill_items(drains, trips, dumpsite, fleet, ground_truth):
    """Every DynamoDB item the seed writes (plan section 4)."""
    items = []
    claimed = sum(ds.DRAIN_CLAIMED[drain["properties"]["drainId"]] for drain in drains)

    items.append(
        {
            "pk": ds.bill_pk(),
            "sk": "META",
            "billId": ds.BILL_ID,
            "contractor": "Simulated Contractor Pvt Ltd",
            "ward": "Ward (set from drains.geojson)",
            "ratePerTonne": ds.RATE_PER_TONNE,
            "claimedTonnes": claimed,
            "claimedRupees": ds.rupees(claimed),
            "workWindowStart": ds.WORK_WINDOW[0],
            "workWindowEnd": ds.WORK_WINDOW[1],
            "status": "PENDING",
            "simulated": True,
        }
    )

    for feature in drains:
        properties = feature["properties"]
        drain_id = properties["drainId"]
        items.append(
            {
                "pk": ds.bill_pk(),
                "sk": ds.drain_sk(drain_id),
                "drainId": drain_id,
                "name": properties["name"],
                "lengthM": properties["lengthM"],
                "widthM": properties["widthM"],
                "depthM": properties["depthM"],
                "plausibleMaxTonnes": properties["plausibleMaxTonnes"],
                "claimedTonnes": ds.DRAIN_CLAIMED[drain_id],
                "claimedRupees": ds.rupees(ds.DRAIN_CLAIMED[drain_id]),
                "geofence": feature["geometry"],
                "centreline": properties.get("centreline"),
                "verdict": None,
                "decision": None,
                "note": None,
            }
        )

    for trip in trips:
        items.append(
            {
                "pk": ds.bill_pk(),
                "sk": ds.trip_sk(trip["drainId"], trip["tripNo"]),
                "tripId": trip["tripId"],
                "drainId": trip["drainId"],
                "tripNo": trip["tripNo"],
                "vehicleNo": trip["vehicleNo"],
                "claimedTonnes": trip["claimedTonnes"],
                "traceKey": trip["traceKey"],
                "slipKey": trip["slipKey"],
                "startTime": trip["startTime"],
                "arrivalTime": trip["arrivalTime"],
                "endTime": trip["endTime"],
                "verdict": None,
                "failedRules": [],
            }
        )

    for vehicle in fleet:
        items.append(
            {
                "pk": ds.vehicle_pk(vehicle["vehicleNo"]),
                "sk": "META",
                "vehicleNo": vehicle["vehicleNo"],
                "capacityTonnes": vehicle["capacityTonnes"],
                "source": "simulated stand-in for the Vahan registry",
            }
        )

    site = dumpsite["features"][0]
    items.append(
        {
            "pk": ds.dumpsite_pk("D1"),
            "sk": "META",
            "dumpsiteId": "D1",
            "name": site["properties"]["name"],
            "geofence": site["geometry"],
            "center": site["properties"]["center"],
            "placeholder": site["properties"].get("placeholder", False),
        }
    )

    # The expected answers travel with the data, so Day 2 can diff against them.
    items.append(
        {
            "pk": ds.bill_pk(),
            "sk": "GROUNDTRUTH",
            "totals": ground_truth["totals"],
            "drains": ground_truth["drains"],
        }
    )

    return items


# --------------------------------------------------------------------- live
def upload_all(items, bucket, concurrency, pace_s, dry_run):
    from common import awsclients

    client = awsclients.client("s3")
    done = {"count": 0, "bytes": 0}

    def upload(item):
        content_type = mimetypes.guess_type(item["key"])[0] or "application/octet-stream"
        client.put_object(
            Bucket=bucket,
            Key=item["key"],
            Body=item["path"].read_bytes(),
            ContentType=content_type,
        )
        done["count"] += 1
        done["bytes"] += item["size"]
        if done["count"] % 20 == 0:
            print(f"  uploaded {done['count']}/{len(items)}")
        if pace_s:
            time.sleep(pace_s)

    if dry_run:
        return done

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(upload, items))

    return done


def write_items(items, dry_run):
    if dry_run:
        return 0
    from common import store

    return store.put_many(items)


# --------------------------------------------------------------------- main
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="actually upload and write. Costs money.")
    parser.add_argument("--yes", action="store_true", help="skip the --live confirmation")
    parser.add_argument("--bucket", default=None, help="evidence bucket (default $EVIDENCE_BUCKET)")
    parser.add_argument("--table", default=None, help="DynamoDB table (default $TABLE_NAME or siltproof)")
    parser.add_argument("--evidence", default=None, help="default data/out/evidence")
    parser.add_argument("--photos-dir", default=None, help="folder of real photos, replacing the generated ones")
    parser.add_argument("--photo-map", default=None, help="CSV mapping real photos to drains")
    parser.add_argument("--max-slips", type=int, default=0, help="upload at most this many slips")
    parser.add_argument("--skip-photos", action="store_true")
    parser.add_argument("--concurrency", type=int, default=3,
                        help="parallel uploads; matches the ingest function's reserved concurrency")
    parser.add_argument("--pace", type=float, default=0.4, help="seconds to wait after each upload")
    args = parser.parse_args(argv)

    import os

    bucket = args.bucket or os.environ.get("EVIDENCE_BUCKET", "")
    table = args.table or os.environ.get("TABLE_NAME", "siltproof")
    os.environ.setdefault("TABLE_NAME", table)

    evidence_root = pathlib.Path(args.evidence or (ds.OUT / "evidence"))

    for name in ("drains.geojson", "dumpsite.geojson", "trips.json", "ground_truth.json"):
        if not (ds.OUT / name).exists():
            print(f"Missing {ds.relative(ds.OUT / name)}.")
            print("Run data/osm_drains.py, then data/gen_trips.py, gen_slips.py, gen_photos.py.")
            return 2

    drains = sorted(
        ds.load_json(ds.OUT / "drains.geojson")["features"],
        key=lambda feature: int(feature["properties"]["drainId"]),
    )
    dumpsite = ds.load_json(ds.OUT / "dumpsite.geojson")
    trips = ds.load_json(ds.OUT / "trips.json")["trips"]
    ground_truth = ds.load_json(ds.OUT / "ground_truth.json")
    fleet = ds.vehicles()

    # ---- what would be uploaded
    generated = walk_evidence(evidence_root)
    photos = [item for item in generated if item["key"].startswith("photos/")]
    slips = [item for item in generated if item["key"].startswith("slips/")]
    traces = [item for item in generated if item["key"].startswith("traces/")]

    missing_photos = []
    if args.photos_dir:
        if not args.photo_map:
            print("--photos-dir needs --photo-map (see data/PHOTO_MAPPING.md)")
            return 2
        photos, missing_photos = real_photo_items(args.photos_dir, args.photo_map)

    if args.skip_photos:
        photos = []
    if args.max_slips:
        slips = slips[: args.max_slips]

    uploads = photos + slips + traces
    items = bill_items(drains, trips, dumpsite, fleet, ground_truth)

    bedrock_calls = len(photos)
    textract_calls = len(slips)
    estimate = (
        textract_calls * COST_PER_TEXTRACT_QUERIES_PAGE
        + bedrock_calls * COST_PER_BEDROCK_PHOTO
    )

    ds.banner("SiltProof seed" + ("" if args.live else " (dry run)"))
    print(f"bucket          {bucket or '(unset)'}")
    print(f"table           {table}")
    print(f"photos          {len(photos)}" + (" (real)" if args.photos_dir else " (generated)"))
    print(f"slips           {len(slips)}")
    print(f"traces          {len(traces)}")
    print(f"uploads         {len(uploads)} objects, {sum(i['size'] for i in uploads) / 1e6:.1f} MB")
    print(f"dynamodb items  {len(items)}")

    if missing_photos:
        print(f"\nphoto_map lists {len(missing_photos)} files that are not in the folder:")
        for name in missing_photos[:10]:
            print(f"  - {name}")

    print("\nBillable calls this triggers, through the ingest Lambda:")
    print(f"  Textract AnalyzeDocument (QUERIES)  {textract_calls}")
    print(f"  Bedrock Converse (vision)           {bedrock_calls}")
    print(f"  rough list-price total              about ${estimate:.2f}")
    print("  (re-seeding is cheaper: ingest skips objects whose ETag is unchanged)")

    if not args.live:
        dump = {
            "mode": "dry-run",
            "bucket": bucket,
            "table": table,
            "uploads": [
                {
                    "key": item["key"],
                    "bytes": item["size"],
                    "contentType": mimetypes.guess_type(item["key"])[0],
                }
                for item in uploads
            ],
            "items": items,
            "estimatedCalls": {"textract": textract_calls, "bedrock": bedrock_calls},
        }
        path = ds.save_json(ds.OUT / "seed_dump.json", dump)
        print(f"\nDry run. Nothing was uploaded or written.")
        print(f"Wrote the full plan to {ds.relative(path)}")
        print("Re-run with --live once the AWS account is active.")
        return 0

    if not bucket:
        print("\n--live needs a bucket: pass --bucket or set EVIDENCE_BUCKET")
        print("(it is the EvidenceBucketName output of the SAM stack)")
        return 2

    if not args.yes:
        reply = input("\nUpload and write all of the above? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Nothing uploaded.")
            return 1

    print(f"\nwriting {len(items)} DynamoDB items...")
    write_items(items, dry_run=False)

    print(f"uploading {len(uploads)} objects at concurrency {args.concurrency}...")
    started = time.time()
    done = upload_all(uploads, bucket, args.concurrency, args.pace, dry_run=False)
    print(f"uploaded {done['count']} objects in {time.time() - started:.0f}s")

    print("\nThe ingest Lambda is now running over those objects.")
    print("Check it finished with:")
    print(f"  aws dynamodb scan --table-name {table} --select COUNT "
          f"--filter-expression 'begins_with(pk, :p)' "
          f"--expression-attribute-values '{{\":p\":{{\"S\":\"EVID#\"}}}}'")
    print(f"  expected: {len(photos) + len(slips) + len(traces)} evidence items")
    return 0


if __name__ == "__main__":
    sys.exit(main())
