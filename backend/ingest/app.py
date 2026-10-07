"""S3-triggered evidence ingestion.

Stub for Day 1 morning: it only logs what arrived and which handler will own it.

Day 1 afternoon/evening fills these in (plan section 3):
  photos/  -> EXIF GPS + timestamp, perceptual hash, Bedrock vision verdict
  slips/   -> Textract AnalyzeDocument with QUERIES
  traces/  -> parse GPS points
and writes EVID#<s3key> items to DynamoDB.
"""

import json
import os
import urllib.parse

TABLE_NAME = os.environ.get("TABLE_NAME", "siltproof")
EVIDENCE_BUCKET = os.environ.get("EVIDENCE_BUCKET", "")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "")

# S3 prefix -> the evidence kind it carries.
PREFIX_KINDS = {
    "photos/": "PHOTO",
    "slips/": "SLIP",
    "traces/": "TRACE",
}


def kind_for_key(key):
    for prefix, kind in PREFIX_KINDS.items():
        if key.startswith(prefix):
            return kind
    return "UNKNOWN"


def log(**fields):
    print(json.dumps(fields))


def handle_photo(bucket, key):
    # TODO Day 1 afternoon: EXIF GPS/time + pHash, then Bedrock vision.
    log(event="photo_pending", bucket=bucket, key=key)


def handle_slip(bucket, key):
    # TODO Day 1 afternoon: Textract AnalyzeDocument with QUERIES.
    log(event="slip_pending", bucket=bucket, key=key)


def handle_trace(bucket, key):
    # TODO Day 1 afternoon: parse GPS points into a trip trace.
    log(event="trace_pending", bucket=bucket, key=key)


HANDLERS = {
    "PHOTO": handle_photo,
    "SLIP": handle_slip,
    "TRACE": handle_trace,
}


def lambda_handler(event, context):
    records = event.get("Records", [])
    processed = 0

    for record in records:
        s3 = record.get("s3", {})
        bucket = s3.get("bucket", {}).get("name")
        # S3 event keys are URL-encoded (spaces become '+').
        raw_key = s3.get("object", {}).get("key", "")
        key = urllib.parse.unquote_plus(raw_key)
        kind = kind_for_key(key)

        log(event="evidence_received", bucket=bucket, key=key, kind=kind)

        handler = HANDLERS.get(kind)
        if handler is None:
            log(event="evidence_ignored", bucket=bucket, key=key)
            continue

        handler(bucket, key)
        processed += 1

    return {"processed": processed, "received": len(records)}
