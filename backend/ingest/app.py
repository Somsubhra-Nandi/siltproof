"""S3-triggered evidence ingestion (plan sections 3 and 4).

    photos/  EXIF GPS + timestamp, perceptual hash, Bedrock vision verdict
    slips/   Textract AnalyzeDocument with QUERIES
    traces/  GPS trace summary

All the expensive AI runs here, at ingestion, so clicking "Run Verification"
later is fast deterministic rules only.

Re-processing the same object is idempotent: items are keyed by the S3 key and
overwritten, and an object whose ETag already matches a successfully ingested
item is skipped entirely unless FORCE_REINGEST is set. That makes a re-seed
cheap instead of another 40 Bedrock calls.

Key conventions written by data/seed.py:
    photos/{billId}/drain{drainId}/{role}-{seq}.jpg   role = before|after|load
    slips/{billId}/{drainId}-{tripNo}.png
    traces/{billId}/{drainId}-{tripNo}.json
Anything that does not follow them (a live demo upload, say) still ingests;
the drain and trip fields just come back empty.
"""

import datetime
import json
import os
import re
import sys
import urllib.parse

# Lambda unpacks the whole backend/ tree, so common/ is a sibling package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import awsclients, bedrock, config, photo, store, textract  # noqa: E402
from common.jsonlog import log  # noqa: E402

MAX_BYTES_FOR_AI = 8 * 1024 * 1024  # Bedrock and Textract both cap request size.

KEY_PATTERNS = {
    "PHOTO": re.compile(
        r"^photos/(?P<billId>[^/]+)/drain(?P<drainId>[^/]+)/(?P<role>before|after|load)-(?P<seq>[^.]+)\."
    ),
    "SLIP": re.compile(r"^slips/(?P<billId>[^/]+)/(?P<drainId>[^-/]+)-(?P<tripNo>[^.]+)\."),
    "TRACE": re.compile(r"^traces/(?P<billId>[^/]+)/(?P<drainId>[^-/]+)-(?P<tripNo>[^.]+)\."),
}


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def parse_evidence_key(key, kind):
    """Pull billId / drainId / tripNo / role out of the key, where present."""
    parsed = {"billId": None, "drainId": None, "tripNo": None, "role": None}
    pattern = KEY_PATTERNS.get(kind)
    if pattern is None:
        return parsed

    match = pattern.match(key)
    if match is None:
        return parsed

    parsed.update({name: value for name, value in match.groupdict().items() if name in parsed})
    return parsed


# ---------------------------------------------------------------- s3 access
def fetch_object(bucket, key):
    client = awsclients.client("s3")
    response = client.get_object(Bucket=bucket, Key=key)
    body = response["Body"].read()
    return {
        "bytes": body,
        "size": len(body),
        "etag": (response.get("ETag") or "").strip('"'),
        "contentType": response.get("ContentType"),
        "lastModified": response.get("LastModified"),
    }


def already_ingested(key, etag):
    """True if a successful item for this exact object version exists."""
    if config.force_reingest():
        return False

    kind = config.kind_for_key(key)
    existing = store.get(store.evidence_pk(key), kind)
    if not existing:
        return False

    return existing.get("sourceEtag") == etag and existing.get("status") == "OK"


# ----------------------------------------------------------------- handlers
def handle_photo(bucket, key, obj, meta):
    exif = photo.read_exif(obj["bytes"])
    phash = photo.perceptual_hash(obj["bytes"])

    verdict = None
    problems = list(exif["problems"])

    if phash is None:
        problems.append("phash_failed")

    if obj["size"] > MAX_BYTES_FOR_AI:
        problems.append("too_large_for_bedrock")
        verdict = {
            "cleared": False,
            "load_type": "unclear",
            "confidence": 0.0,
            "notes": "Photo too large to send to Bedrock.",
            "ok": False,
            "problems": ["too_large"],
        }
    else:
        response = bedrock.check_photo(
            obj["bytes"], key=key, content_type=obj["contentType"]
        )
        verdict = bedrock.parse_photo_check(response)

    item = {
        "pk": store.evidence_pk(key),
        "sk": "PHOTO",
        "s3Key": key,
        "bucket": bucket,
        "sourceEtag": obj["etag"],
        "contentType": obj["contentType"],
        "sizeBytes": obj["size"],
        "billId": meta["billId"] or config.bill_id(),
        "drainId": meta["drainId"],
        "role": meta["role"],
        "lat": exif["lat"],
        "lon": exif["lon"],
        "timestamp": exif["timestamp"],
        "hasGps": exif["hasGps"],
        "hasTimestamp": exif["hasTimestamp"],
        "camera": exif.get("camera"),
        "pHash": phash,
        "bedrock": {
            "cleared": verdict["cleared"],
            "load_type": verdict["load_type"],
            "confidence": verdict["confidence"],
            "notes": verdict["notes"],
            "ok": verdict["ok"],
            "modelId": config.vision_model_id(),
        },
        "problems": problems + [f"bedrock:{p}" for p in verdict.get("problems", [])],
        "status": "OK",
        "ingestedAt": now_iso(),
    }

    store.put(item)

    # A second, queryable copy of the hash so rule R3 can find duplicates with
    # one begins_with query instead of scanning every evidence item.
    if phash:
        store.put(
            {
                "pk": store.bill_pk(item["billId"]),
                "sk": f"PHASH#{phash}#{key}",
                "pHash": phash,
                "s3Key": key,
                "drainId": meta["drainId"],
                "role": meta["role"],
                "timestamp": exif["timestamp"],
            }
        )

    log(
        "photo_ingested",
        key=key,
        drain=meta["drainId"],
        role=meta["role"],
        has_gps=exif["hasGps"],
        has_timestamp=exif["hasTimestamp"],
        phash=phash,
        load_type=verdict["load_type"],
        cleared=verdict["cleared"],
        confidence=verdict["confidence"],
        problems=item["problems"],
    )
    return item


def handle_slip(bucket, key, obj, meta):
    response = textract.analyze_document(obj["bytes"], key=key)
    parsed = textract.parse_slip(response)
    fields = parsed["fields"]

    item = {
        "pk": store.evidence_pk(key),
        "sk": "SLIP",
        "s3Key": key,
        "bucket": bucket,
        "sourceEtag": obj["etag"],
        "contentType": obj["contentType"],
        "sizeBytes": obj["size"],
        "billId": meta["billId"] or config.bill_id(),
        "drainId": meta["drainId"],
        "tripNo": meta["tripNo"],
        # Flattened for the rules and the UI...
        "ticketNo": fields["ticketNo"]["value"],
        "vehicleNo": fields["vehicleNo"]["value"],
        "gross": fields["gross"]["value"],
        "tare": fields["tare"]["value"],
        "net": fields["net"]["value"],
        "timeIn": fields["timeIn"]["value"],
        "timeOut": fields["timeOut"]["value"],
        "site": fields["site"]["value"],
        # ...and kept in full so the drill-down can show confidence per field.
        "textract": {
            "fields": fields,
            "confidenceAvg": parsed["confidenceAvg"],
            "missingFields": parsed["missingFields"],
            "lowConfidenceFields": parsed["lowConfidenceFields"],
            "modelVersion": parsed["modelVersion"],
        },
        "problems": [f"missing:{name}" for name in parsed["missingFields"]]
        + [f"low_confidence:{name}" for name in parsed["lowConfidenceFields"]],
        "status": "OK",
        "ingestedAt": now_iso(),
    }

    store.put(item)

    log(
        "slip_ingested",
        key=key,
        drain=meta["drainId"],
        trip=meta["tripNo"],
        vehicle=item["vehicleNo"],
        net=item["net"],
        time_in=item["timeIn"],
        confidence_avg=parsed["confidenceAvg"],
        missing=parsed["missingFields"],
    )
    return item


def _trace_points(payload):
    """Accept either {points: [...]} or a bare list of points."""
    if isinstance(payload, dict):
        points = payload.get("points") or payload.get("Points") or []
    elif isinstance(payload, list):
        points = payload
    else:
        points = []

    cleaned = []
    for point in points:
        if isinstance(point, dict):
            lat, lon = point.get("lat"), point.get("lon")
            stamp = point.get("t") or point.get("timestamp")
        elif isinstance(point, (list, tuple)) and len(point) >= 2:
            lon, lat, stamp = point[0], point[1], (point[2] if len(point) > 2 else None)
        else:
            continue

        if lat is None or lon is None:
            continue
        cleaned.append({"lat": float(lat), "lon": float(lon), "t": stamp})

    return cleaned


def _max_gap_seconds(points):
    stamps = []
    for point in points:
        if not point.get("t"):
            continue
        try:
            stamps.append(datetime.datetime.fromisoformat(str(point["t"])))
        except ValueError:
            continue

    if len(stamps) < 2:
        return None, 0

    stamps.sort()
    gaps = [
        (stamps[index + 1] - stamps[index]).total_seconds()
        for index in range(len(stamps) - 1)
    ]
    return round(max(gaps), 1), len(stamps)


def handle_trace(bucket, key, obj, meta):
    problems = []
    try:
        payload = json.loads(obj["bytes"].decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        payload = {}
        problems.append(f"unparseable_trace:{type(exc).__name__}")

    points = _trace_points(payload)
    if not points:
        problems.append("no_points")

    max_gap, timed = _max_gap_seconds(points)

    item = {
        "pk": store.evidence_pk(key),
        "sk": "TRACE",
        "s3Key": key,
        "bucket": bucket,
        "sourceEtag": obj["etag"],
        "contentType": obj["contentType"],
        "sizeBytes": obj["size"],
        "billId": meta["billId"] or config.bill_id(),
        "drainId": meta["drainId"],
        "tripNo": meta["tripNo"],
        "vehicleNo": payload.get("vehicleNo") if isinstance(payload, dict) else None,
        "pointCount": len(points),
        "timedPointCount": timed,
        "maxGapSeconds": max_gap,
        "startTime": points[0]["t"] if points else None,
        "endTime": points[-1]["t"] if points else None,
        "startPoint": [points[0]["lon"], points[0]["lat"]] if points else None,
        "endPoint": [points[-1]["lon"], points[-1]["lat"]] if points else None,
        # The full trace stays in S3; rules R5/R6/R8 load it from there.
        "traceKey": key,
        "problems": problems,
        "status": "OK",
        "ingestedAt": now_iso(),
    }

    store.put(item)

    log(
        "trace_ingested",
        key=key,
        drain=meta["drainId"],
        trip=meta["tripNo"],
        points=len(points),
        max_gap_s=max_gap,
        problems=problems,
    )
    return item


HANDLERS = {"PHOTO": handle_photo, "SLIP": handle_slip, "TRACE": handle_trace}


# ------------------------------------------------------------------ handler
def process_record(bucket, key):
    kind = config.kind_for_key(key)
    handler = HANDLERS.get(kind)

    if handler is None:
        log("evidence_ignored", bucket=bucket, key=key, reason="unknown_prefix")
        return "ignored"

    if key.endswith("/"):
        log("evidence_ignored", bucket=bucket, key=key, reason="folder_marker")
        return "ignored"

    obj = fetch_object(bucket, key)

    if already_ingested(key, obj["etag"]):
        log("evidence_skipped", bucket=bucket, key=key, kind=kind, reason="unchanged")
        return "skipped"

    meta = parse_evidence_key(key, kind)
    handler(bucket, key, obj, meta)
    return "processed"


def record_failure(bucket, key, exc):
    """Keep the failure in DynamoDB so the exit check can see what is missing."""
    kind = config.kind_for_key(key)
    try:
        store.put(
            {
                "pk": store.evidence_pk(key),
                "sk": kind,
                "s3Key": key,
                "bucket": bucket,
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}"[:900],
                "ingestedAt": now_iso(),
            }
        )
    except Exception as write_error:  # DynamoDB itself is down; logs are all we have.
        log("failure_record_failed", key=key, error=str(write_error))


def lambda_handler(event, context):
    counts = {"processed": 0, "skipped": 0, "ignored": 0, "failed": 0}
    records = event.get("Records", []) or []

    for record in records:
        s3 = record.get("s3", {}) or {}
        bucket = (s3.get("bucket") or {}).get("name")
        # S3 event keys arrive URL-encoded, with spaces as '+'.
        key = urllib.parse.unquote_plus((s3.get("object") or {}).get("key", ""))

        if not bucket or not key:
            counts["ignored"] += 1
            continue

        log("evidence_received", bucket=bucket, key=key, kind=config.kind_for_key(key))

        try:
            counts[process_record(bucket, key)] += 1
        except Exception as exc:
            counts["failed"] += 1
            log(
                "evidence_failed",
                bucket=bucket,
                key=key,
                error=f"{type(exc).__name__}: {exc}",
            )
            record_failure(bucket, key, exc)

    log("ingest_done", **counts, received=len(records))
    return {**counts, "received": len(records)}
