#!/usr/bin/env python3
"""Compare what the ingest Lambda read with what the dataset printed.

Run after `seed.py --simulated-vision --live`, before anyone verifies the bill.
Read-only: it only reads DynamoDB.

    python data/check_seed.py                  # plan only, touches nothing
    python data/check_seed.py --live           # read and compare
    python data/check_seed.py --live --verify  # then run the rules, if all match

Blocking: anything a verdict depends on. For a slip that is the vehicle
number (R9), the net weight (R7) and the time-in (R8); for a photo, the
planted verdict stored as simulated (R4). Any blocking mismatch means the
seeded bill would not reproduce data/out/ground_truth.json, so --verify
refuses. Other slip fields (ticket, gross, tare, time-out, site) are reported
as warnings: the garbled slips are meant to lose some of them.

--verify runs POST /verify in this process with your own AWS credentials and
B1_OPERATOR_ROUTES=enabled set here only; the public route stays read-only.
It calls no model: the rules are pure functions over what is stored. It then
compares the bill's totals with the ground truth.
"""

import argparse
import json
import os
import pathlib
import sys

DATA = pathlib.Path(__file__).resolve().parent
REPO = DATA.parent
sys.path.insert(0, str(DATA))
sys.path.insert(0, str(REPO / "backend"))

import dataset as ds  # noqa: E402

BLOCKING_SLIP = ("vehicleNo", "net", "timeIn")
WARNING_SLIP = ("ticketNo", "gross", "tare", "timeOut", "site")
WEIGHT_TOLERANCE_T = 0.005


def _same(name, read, printed):
    if read is None or printed is None:
        return read == printed
    if name in ("gross", "tare", "net"):
        try:
            return abs(float(read) - float(printed)) <= WEIGHT_TOLERANCE_T
        except (TypeError, ValueError):
            return False
    clean = lambda value: " ".join(str(value).upper().split())  # noqa: E731
    return clean(read) == clean(printed)


def compare(manifest, get_item, trace_keys):
    """get_item(s3_key, kind) -> the ingested item or None. Returns a report."""
    report = {"blocking": [], "warnings": [], "checked": {"slips": 0, "photos": 0, "traces": 0}}

    for key, printed in sorted((manifest.get("slips") or {}).items()):
        item = get_item(key, "SLIP")
        report["checked"]["slips"] += 1
        if not item or item.get("status") != "OK":
            report["blocking"].append(f"{key}: not ingested ({(item or {}).get('status')})")
            continue
        for name in BLOCKING_SLIP + WARNING_SLIP:
            if not _same(name, item.get(name), printed.get(name)):
                line = f"{key}: {name} read {item.get(name)!r}, printed {printed.get(name)!r}"
                report["blocking" if name in BLOCKING_SLIP else "warnings"].append(line)

    for key, planned in sorted((manifest.get("photos") or {}).items()):
        item = get_item(key, "PHOTO")
        report["checked"]["photos"] += 1
        vision = (item or {}).get("bedrock") or {}
        if not item or item.get("status") != "OK":
            report["blocking"].append(f"{key}: not ingested ({(item or {}).get('status')})")
        elif not vision.get("simulated") or vision.get("modelId"):
            report["blocking"].append(f"{key}: not stored as simulated (model {vision.get('modelId')})")
        elif (vision.get("cleared"), vision.get("load_type")) != (planned["cleared"], planned["loadType"]):
            report["blocking"].append(
                f"{key}: stored {vision.get('cleared')}/{vision.get('load_type')}, "
                f"planned {planned['cleared']}/{planned['loadType']}")

    for key in trace_keys:
        item = get_item(key, "TRACE")
        report["checked"]["traces"] += 1
        if not item or item.get("status") != "OK":
            report["blocking"].append(f"{key}: not ingested ({(item or {}).get('status')})")
    return report


def verify_in_process(bill_id):
    """POST /verify and GET /bill through the API handler, in this process only."""
    os.environ["B1_OPERATOR_ROUTES"] = "enabled"
    from api import app

    def call(route, bill):
        reply = app.lambda_handler({"routeKey": route, "pathParameters": {"billId": bill},
                                    "body": "{}"}, None)
        return reply["statusCode"], json.loads(reply["body"])

    status, body = call("POST /verify/{billId}", bill_id)
    if status != 200:
        return status, body
    return call("GET /bill/{billId}", bill_id)


def totals_match(summary, expected):
    keys = ("claimedTonnes", "verifiedTonnes", "reviewTonnes", "heldTonnes")
    return all(abs(float(summary[k]) - float(expected[k])) < 0.01 for k in keys), \
        {k: (summary.get(k), expected.get(k)) for k in keys}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="read the deployed table")
    parser.add_argument("--verify", action="store_true",
                        help="if nothing blocks, run the rules and compare with the ground truth")
    parser.add_argument("--table", default=None, help="DynamoDB table (default $TABLE_NAME or siltproof)")
    parser.add_argument("--show", type=int, default=15, help="lines to print per list")
    args = parser.parse_args(argv)

    manifest = ds.load_json(ds.OUT / "mock_manifest.json")
    trips = ds.load_json(ds.OUT / "trips.json")["trips"]
    ground_truth = ds.load_json(ds.OUT / "ground_truth.json")
    bill_id = ground_truth.get("billId", "B1")
    trace_keys = sorted({trip["traceKey"] for trip in trips if trip.get("traceKey")})

    ds.banner("SiltProof seed check" + ("" if args.live else " (dry run)"))
    print(f"slips   {len(manifest.get('slips') or {})}  (blocking: {', '.join(BLOCKING_SLIP)})")
    print(f"photos  {len(manifest.get('photos') or {})}  (blocking: simulated verdict as planned)")
    print(f"traces  {len(trace_keys)}")
    print("calls   DynamoDB reads only" + ("; then the rules in this process" if args.verify else ""))
    if not args.live:
        print("\nDry run. Nothing was read. Re-run with --live after the seed has ingested.")
        return 0

    if args.table:
        os.environ["TABLE_NAME"] = args.table
    from common import store

    report = compare(manifest, lambda key, kind: store.get(store.evidence_pk(key), kind), trace_keys)
    print(f"\nchecked {report['checked']}")
    for title in ("blocking", "warnings"):
        lines = report[title]
        print(f"{title}: {len(lines)}")
        for line in lines[: args.show]:
            print(f"  {line}")
        if len(lines) > args.show:
            print(f"  ... {len(lines) - args.show} more")

    if report["blocking"]:
        print("\nDo not verify: the bill would not reproduce the ground truth. Re-seed the "
              "mismatched objects, or decide which side is wrong.")
        return 1
    if not args.verify:
        print("\nAll blocking fields match. Re-run with --verify to run the rules.")
        return 0

    status, bill = verify_in_process(bill_id)
    if status != 200:
        print(f"\nverification failed: {status} {bill}")
        return 1
    ok, detail = totals_match(bill["summary"], ground_truth["totals"])
    print(f"\ntotals (seeded, expected): {detail}")
    print("matches the ground truth" if ok else "DOES NOT match the ground truth")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
