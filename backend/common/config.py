"""Runtime configuration, read from the environment.

Read through functions rather than module constants so tests can change the
environment between cases.
"""

import os

# Evidence prefixes -> the sk of the DynamoDB item the ingest Lambda writes.
PREFIX_KINDS = {
    "photos/": "PHOTO",
    "slips/": "SLIP",
    "traces/": "TRACE",
}

# Illustrative contractor rate (plan section 6).
RATE_PER_TONNE = 1800

# Loose silt, tonnes per cubic metre, for rule R10.
SILT_DENSITY = 1.4

# Metres: a photo inside the drain buffer passes R1, between the buffer and
# this distance is a soft fail, beyond it is a hard fail. See DECISIONS.md.
R1_SOFT_BAND_M = 60.0

# Drain centrelines are buffered by this much to make the geofence.
DRAIN_BUFFER_M = 30.0


def table_name():
    return os.environ.get("TABLE_NAME", "siltproof")


def evidence_bucket():
    return os.environ.get("EVIDENCE_BUCKET", "")


def region():
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-south-1"


def text_model_id():
    """Model for the Day 2 evidence summary."""
    return os.environ.get("BEDROCK_MODEL_ID", "apac.amazon.nova-pro-v1:0")


def vision_model_id():
    """Model for the before/after photo check (must accept a forced toolChoice)."""
    return os.environ.get(
        "BEDROCK_VISION_MODEL_ID", "apac.amazon.nova-pro-v1:0"
    )


def mock_aws():
    """True when the providers should return canned responses."""
    return os.environ.get("MOCK_AWS", "") not in ("", "0", "false", "False")


def mock_manifest_path():
    """Optional generator manifest that makes mock responses match the data."""
    return os.environ.get("MOCK_MANIFEST_PATH", "")


def force_reingest():
    """Re-run extraction even if an evidence item already exists."""
    return os.environ.get("FORCE_REINGEST", "") not in ("", "0", "false", "False")


def bill_id():
    return os.environ.get("BILL_ID", "B1")


def operator_routes_enabled():
    """The unauthenticated routes that spend or write the shared bill:
    POST /upload-url and summary refresh (each can start a Bedrock or Textract
    call with no quota), POST /verify and POST /decision. Off in a live
    deployment unless B1_OPERATOR_ROUTES=enabled; always on in mock mode."""
    return mock_aws() or os.environ.get("B1_OPERATOR_ROUTES", "") == "enabled"


def kind_for_key(key):
    for prefix, kind in PREFIX_KINDS.items():
        if key.startswith(prefix):
            return kind
    return "UNKNOWN"
