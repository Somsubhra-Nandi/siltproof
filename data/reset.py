#!/usr/bin/env python3
"""Put the demo back to its clean, pre-verification state.

    python data/reset.py                 # dry run: print the plan, change nothing
    python data/reset.py --live          # do it
    python data/reset.py --live --hard   # also delete the extracted facts

Normal reset (what you want between recording takes):
  * clears verdict, decision, note and failedRules on every drain and trip
  * deletes anything uploaded during a live-demo take, under photos/live/,
    slips/live/ and traces/live/, together with its evidence items
  * leaves the seeded evidence and its extracted facts alone, so nothing has
    to be sent to Textract or Bedrock again

--hard also deletes every EVID# item and the pHash index. The next upload will
re-extract everything, which costs money again. Use it only if the extracted
facts are wrong, not between takes.
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import dataset as ds  # noqa: E402

LIVE_PREFIXES = ("photos/live/", "slips/live/", "traces/live/")

# Attributes a verification run writes, which a reset has to clear.
DRAIN_FIELDS = {"verdict": None, "decision": None, "note": None,
                "summary": None, "summaryModelId": None, "decidedAt": None}
TRIP_FIELDS = {"verdict": None, "failedRules": [], "ruleDetail": None}


def local_plan():
    """Counts from the generated files, so a dry run needs no AWS."""
    drains = trips = 0

    if (ds.OUT / "drains.geojson").exists():
        drains = len(ds.load_json(ds.OUT / "drains.geojson")["features"])
    if (ds.OUT / "trips.json").exists():
        trips = len(ds.load_json(ds.OUT / "trips.json")["trips"])

    return {"drains": drains, "trips": trips}


def clear_verdicts(items, dry_run):
    from common import store

    cleared = 0
    for item in items:
        fields = DRAIN_FIELDS if item["sk"].startswith("DRAIN#") else TRIP_FIELDS
        if not any(item.get(name) not in (None, [], "") for name in fields):
            continue
        if not dry_run:
            store.put({**item, **fields})
        cleared += 1

    return cleared


def find_live_objects(bucket):
    from common import awsclients

    client = awsclients.client("s3")
    keys = []

    for prefix in LIVE_PREFIXES:
        token = None
        while True:
            kwargs = {"Bucket": bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            response = client.list_objects_v2(**kwargs)
            keys.extend(obj["Key"] for obj in response.get("Contents", []) or [])
            if not response.get("IsTruncated"):
                break
            token = response.get("NextContinuationToken")

    return keys


def delete_objects(bucket, keys, dry_run):
    if dry_run or not keys:
        return 0

    from common import awsclients

    client = awsclients.client("s3")
    for start in range(0, len(keys), 1000):
        batch = keys[start:start + 1000]
        client.delete_objects(
            Bucket=bucket, Delete={"Objects": [{"Key": key} for key in batch]}
        )

    return len(keys)


def delete_items(keys_and_sks, dry_run):
    if dry_run or not keys_and_sks:
        return 0

    from common import store

    handle = store.table()
    with handle.batch_writer() as batch:
        for pk, sk in keys_and_sks:
            batch.delete_item(Key={"pk": pk, "sk": sk})

    return len(keys_and_sks)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="actually change things")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation")
    parser.add_argument("--hard", action="store_true",
                        help="also delete extracted facts, forcing paid re-extraction")
    parser.add_argument("--bucket", default=None, help="default $EVIDENCE_BUCKET")
    parser.add_argument("--table", default=None, help="default $TABLE_NAME or siltproof")
    args = parser.parse_args(argv)

    import os

    bucket = args.bucket or os.environ.get("EVIDENCE_BUCKET", "")
    table = args.table or os.environ.get("TABLE_NAME", "siltproof")
    os.environ.setdefault("TABLE_NAME", table)

    ds.banner("SiltProof reset" + ("" if args.live else " (dry run)"))
    print(f"bucket      {bucket or '(unset)'}")
    print(f"table       {table}")
    print(f"mode        {'hard - extracted facts deleted too' if args.hard else 'normal'}")

    if not args.live:
        counts = local_plan()
        print(f"\nWould clear verdicts and decisions on:")
        print(f"  {counts['drains']} drain items")
        print(f"  {counts['trips']} trip items")
        print(f"Would delete anything under: {', '.join(LIVE_PREFIXES)}")
        if args.hard:
            print("Would delete every EVID# item and the PHASH# index.")
            print("The next upload would re-run Textract and Bedrock, at full cost.")
        print("\nDry run. Nothing changed. Re-run with --live.")
        return 0

    if not bucket:
        print("\n--live needs a bucket: pass --bucket or set EVIDENCE_BUCKET")
        return 2

    from common import store

    bill_items = store.query_pk(ds.bill_pk())
    verdict_items = [
        item for item in bill_items
        if item["sk"].startswith("DRAIN#") or item["sk"].startswith("TRIP#")
    ]
    phash_items = [item for item in bill_items if item["sk"].startswith("PHASH#")]
    live_keys = find_live_objects(bucket)

    print(f"\nfound       {len(verdict_items)} drain/trip items")
    print(f"            {len(live_keys)} objects from live demo uploads")
    if args.hard:
        print(f"            {len(phash_items)} pHash index entries")

    if not args.yes:
        reply = input("\nReset? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Nothing changed.")
            return 1

    cleared = clear_verdicts(verdict_items, dry_run=False)
    print(f"cleared     {cleared} verdict/decision fields")

    deleted_objects = delete_objects(bucket, live_keys, dry_run=False)
    deleted_items = delete_items(
        [(ds.evidence_pk(key), kind)
         for key in live_keys
         for kind in ("PHOTO", "SLIP", "TRACE")],
        dry_run=False,
    )
    print(f"deleted     {deleted_objects} live-upload objects and their evidence items")

    if args.hard:
        evidence_keys = []
        from common import awsclients

        client = awsclients.client("s3")
        for prefix in ("photos/", "slips/", "traces/"):
            token = None
            while True:
                kwargs = {"Bucket": bucket, "Prefix": prefix}
                if token:
                    kwargs["ContinuationToken"] = token
                response = client.list_objects_v2(**kwargs)
                evidence_keys.extend(obj["Key"] for obj in response.get("Contents", []) or [])
                if not response.get("IsTruncated"):
                    break
                token = response.get("NextContinuationToken")

        delete_items(
            [(ds.evidence_pk(key), kind)
             for key in evidence_keys
             for kind in ("PHOTO", "SLIP", "TRACE")],
            dry_run=False,
        )
        delete_items([(item["pk"], item["sk"]) for item in phash_items], dry_run=False)
        print(f"deleted     extracted facts for {len(evidence_keys)} objects, and the pHash index")
        print("\nRe-upload the evidence to re-extract. That costs money again:")
        print("  python data/seed.py --live")

    print("\nClean. Open the app and the bill is unverified again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
