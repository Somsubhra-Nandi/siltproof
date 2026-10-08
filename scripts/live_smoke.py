#!/usr/bin/env python3
"""Live smoke test: one slip, one photo and one route through real AWS.

Dry-run by default: prints the call plan and the billable-call estimate and
stops. With --live it

1. calls Textract, Bedrock (Converse + toolUse) and geo-routes once each from
   here, saves the raw responses under tests/fixtures/live/, and runs our
   parsers over them;
2. uploads the same slip and photo, plus a trace built from the route, to the
   deployed evidence bucket under bill SMOKE, and waits for the ingest Lambda
   to write its DynamoDB items;
3. writes the findings lists from the offline demo snapshot into DynamoDB
   under BILL#SMOKE and reads them back, to prove the table accepts them.

Everything it writes lives under bill SMOKE, never B1, and is deleted at the
end unless --keep is passed.

    python scripts/live_smoke.py                      # plan only
    python scripts/live_smoke.py --live
    python scripts/live_smoke.py --live --photo ~/IMG_4471.jpg

Needs EVIDENCE_BUCKET (from the stack outputs) in the environment or .env.
"""

import argparse
import datetime
import glob
import json
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

LIVE_DIR = REPO / "tests" / "fixtures" / "live"
DEMO_DIR = REPO / "frontend" / "public" / "data" / "demo"
SMOKE_BILL = "SMOKE"

DEFAULT_SLIP = REPO / "data" / "out" / "evidence" / "slips" / "B1" / "9-001.png"
DEFAULT_PHOTO = REPO / "data" / "out" / "evidence" / "photos" / "B1" / "drain9" / "load-01.jpg"

# Drain 9 area to the dumpsite would be ideal, but any real road works: the
# point is the live response shape.
ROUTE_ORIGIN = [72.8777, 19.0760]
ROUTE_DESTINATION = [72.8479, 19.1197]


def load_dotenv():
    path = REPO / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if value and name not in os.environ:
            os.environ[name] = value


def strip_metadata(response):
    """Drop ResponseMetadata (request ids, headers): not part of the shape."""
    return {k: v for k, v in response.items() if k != "ResponseMetadata"}


def save(name, payload):
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    path = LIVE_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    print(f"  saved {path.relative_to(REPO)}")
    return path


def demo_findings():
    """drainId -> every trip finding in the offline snapshot for that drain."""
    found = {}
    for path in sorted(glob.glob(str(DEMO_DIR / "drain-*.json"))):
        drain = json.loads(pathlib.Path(path).read_text())
        findings = list(drain.get("findings") or [])
        for trip in drain.get("trips") or []:
            findings.extend(trip.get("findings") or [])
        if findings:
            found[str(drain["drainId"])] = findings
    return found


def print_plan(args, slip, photo):
    findings = demo_findings()
    print("Live smoke test plan (bill SMOKE, region ap-south-1)\n")
    print(f"  slip   {slip}")
    print(f"  photo  {photo}")
    print(f"  route  {ROUTE_ORIGIN} -> {ROUTE_DESTINATION}\n")
    print("Billable calls:")
    print("  Textract AnalyzeDocument QUERIES   2 pages  (direct + ingest Lambda)  ~$0.03")
    print("  Bedrock Converse, Haiku 4.5 vision 2 calls  (direct + ingest Lambda)  ~$0.01")
    print("  geo-routes CalculateRoutes         1 call                              <$0.01")
    print(f"  DynamoDB on-demand writes          ~{len(findings) + 10} items                       <$0.01")
    print("  S3 PUT/GET, 3 Lambda invocations                                       <$0.01")
    print("  Total                                                                  ~$0.05")
    print(f"\nCleanup: {'kept (--keep)' if args.keep else 'SMOKE objects and items deleted at the end'}")


# ------------------------------------------------------------- direct calls
def direct_calls(slip_bytes, photo_bytes, photo_name, skip):
    """One call per service. A failing service is reported, not fatal."""
    from common import bedrock, location, textract

    print("\n1. Direct calls, raw responses saved, parsers run")
    results = {}

    def textract_step():
        raw = strip_metadata(textract.analyze_document(slip_bytes, key="smoke-slip"))
        save("textract_analyze_document", raw)
        parsed = textract.parse_slip(raw)
        print(
            "  textract: "
            + ", ".join(f"{k}={v['value']!r}" for k, v in parsed["fields"].items())
            + f"  missing={parsed['missingFields']}"
        )
        return parsed, not parsed["missingFields"]

    def bedrock_step():
        raw = strip_metadata(bedrock.check_photo(photo_bytes, key=photo_name))
        save("bedrock_converse_photo", raw)
        parsed = bedrock.parse_photo_check(raw)
        print(
            f"  bedrock: source={parsed['source']} ok={parsed['ok']} "
            f"load_type={parsed['load_type']} cleared={parsed['cleared']} "
            f"confidence={parsed['confidence']} problems={parsed['problems']}"
        )
        return parsed, parsed["ok"]

    def route_step():
        raw = strip_metadata(location.calculate_route(ROUTE_ORIGIN, ROUTE_DESTINATION))
        save("location_calculate_routes", raw)
        parsed = location.parse_route(raw)
        print(
            f"  route: ok={parsed['ok']} points={len(parsed['points'])} "
            f"distanceM={parsed['distanceM']} durationS={parsed['durationS']}"
        )
        return parsed, parsed["ok"]

    for name, step in (("textract", textract_step), ("bedrock", bedrock_step), ("route", route_step)):
        if name in skip:
            print(f"  {name}: skipped")
            continue
        try:
            results[name] = step()
        except Exception as exc:  # report and carry on with the other services
            print(f"  {name}: FAILED {type(exc).__name__}: {exc}")
            results[name] = (None, False)
    return results


# ---------------------------------------------------------- the real pipeline
def trace_from_route(points):
    start = datetime.datetime(2026, 10, 8, 9, 0, tzinfo=datetime.timezone.utc)
    step = max(1, len(points) // 60)
    sampled = points[::step]
    return {
        "vehicleNo": "MH 01 AB 1234",
        "points": [
            {"lon": lon, "lat": lat, "t": (start + datetime.timedelta(seconds=30 * i)).isoformat()}
            for i, (lon, lat) in enumerate(sampled)
        ],
    }


def pipeline(slip_bytes, photo_bytes, photo_ext, route_points):
    from common import awsclients, config, store

    bucket = config.evidence_bucket()
    s3 = awsclients.client("s3")
    keys = {
        "SLIP": f"slips/{SMOKE_BILL}/9-001.png",
        "PHOTO": f"photos/{SMOKE_BILL}/drain9/load-01{photo_ext}",
        "TRACE": f"traces/{SMOKE_BILL}/9-001.json",
    }
    bodies = {
        "SLIP": (slip_bytes, "image/png"),
        "PHOTO": (photo_bytes, "image/png" if photo_ext == ".png" else "image/jpeg"),
        "TRACE": (json.dumps(trace_from_route(route_points)).encode(), "application/json"),
    }

    print(f"\n2. Ingest pipeline via s3://{bucket}")
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for kind, key in keys.items():
        body, content_type = bodies[kind]
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)
        print(f"  put {key}")

    items = {}
    deadline = time.time() + 120
    while len(items) < len(keys) and time.time() < deadline:
        time.sleep(4)
        for kind, key in keys.items():
            if kind in items:
                continue
            item = store.get(store.evidence_pk(key), kind)
            if item and item.get("ingestedAt", "") >= started:
                items[kind] = item

    for kind, key in keys.items():
        item = items.get(kind)
        if item is None:
            print(f"  {kind:<5} TIMED OUT waiting for {store.evidence_pk(key)}")
            continue
        summary = {
            "SLIP": lambda i: f"net={i.get('net')} vehicle={i.get('vehicleNo')} timeIn={i.get('timeIn')}",
            "PHOTO": lambda i: f"gps={i.get('hasGps')} pHash={i.get('pHash')} bedrock={i.get('bedrock')}",
            "TRACE": lambda i: f"points={i.get('pointCount')} maxGap={i.get('maxGapSeconds')}",
        }[kind](item)
        print(f"  {kind:<5} status={item.get('status')} {summary} problems={item.get('problems')}")
        if item.get("error"):
            print(f"        error={item['error']}")

    return keys, items


def findings_roundtrip():
    from common import store

    print("\n3. DynamoDB accepts the findings lists")
    written = []
    ok = True
    for drain_id, findings in demo_findings().items():
        item = {
            "pk": store.bill_pk(SMOKE_BILL),
            "sk": store.trip_sk(drain_id, "findings"),
            "ruleDetail": findings,
        }
        store.put(item)
        written.append(item["sk"])
        back = store.get(item["pk"], item["sk"])
        # JSON round trip both sides, so 14 and 14.0 compare equal.
        same = json.loads(json.dumps(back["ruleDetail"])) == json.loads(json.dumps(findings))
        ok = ok and same
        print(f"  drain {drain_id:>2}: {len(findings)} findings  {'round-trips' if same else 'DIFFERS'}")
    return ok, written


def cleanup(keys, written):
    from common import awsclients, config, store

    bucket = config.evidence_bucket()
    s3 = awsclients.client("s3")
    table = store.table()
    for kind, key in keys.items():
        s3.delete_object(Bucket=bucket, Key=key)
        table.delete_item(Key={"pk": store.evidence_pk(key), "sk": kind})
    # The ingest Lambda also writes a PHASH#... copy under the bill.
    response = table.query(
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={":pk": store.bill_pk(SMOKE_BILL)},
    )
    for item in response.get("Items", []):
        table.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
    print(f"\nCleaned up {len(keys)} S3 objects and the BILL#{SMOKE_BILL} / EVID items.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="actually call AWS")
    parser.add_argument("--slip", default=str(DEFAULT_SLIP))
    parser.add_argument("--photo", default=str(DEFAULT_PHOTO), help="e.g. a phone photo with GPS")
    parser.add_argument("--keep", action="store_true", help="leave the SMOKE objects and items in place")
    parser.add_argument(
        "--skip",
        default="",
        help="comma-separated steps to skip: textract,bedrock,route,pipeline,findings",
    )
    args = parser.parse_args()

    load_dotenv()
    os.environ.pop("MOCK_AWS", None)
    os.environ.setdefault("AWS_REGION", "ap-south-1")

    slip = pathlib.Path(args.slip).expanduser()
    photo = pathlib.Path(args.photo).expanduser()
    print_plan(args, slip, photo)

    if not args.live:
        print("\nDry run. Pass --live to make these calls.")
        return 0

    from common import config

    if not config.evidence_bucket():
        print("\nEVIDENCE_BUCKET is not set (stack output EvidenceBucketName).")
        return 2

    skip = {name.strip() for name in args.skip.split(",") if name.strip()}
    slip_bytes = slip.read_bytes()
    photo_bytes = photo.read_bytes()

    direct = direct_calls(slip_bytes, photo_bytes, photo.name, skip)
    checks = [ok for _, ok in direct.values()]

    keys, written = {}, []
    if "pipeline" in skip:
        print("\n2. Ingest pipeline: skipped")
    else:
        route = (direct.get("route") or (None, False))[0]
        if route is None:
            from common import location

            route = location.parse_route(location.calculate_route(ROUTE_ORIGIN, ROUTE_DESTINATION))
        keys, items = pipeline(slip_bytes, photo_bytes, photo.suffix.lower(), route["points"])
        checks.append(len(items) == len(keys))
        checks.extend(item.get("status") == "OK" for item in items.values())

    if "findings" in skip:
        print("\n3. Findings round trip: skipped")
    else:
        findings_ok, written = findings_roundtrip()
        checks.append(findings_ok)

    if not args.keep and (keys or written):
        cleanup(keys, written)

    print("\nSMOKE " + ("PASS" if checks and all(checks) else "NEEDS A LOOK"))
    return 0 if checks and all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
