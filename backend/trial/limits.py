"""Every trial limit in one place, read through functions so tests can change
them. The numbers are documented in docs/JUDGE-TRIAL-API.md sections 9 and 16.
"""

import os

MB = 1024 * 1024

TRIAL_LIFETIME_S = 48 * 3600
QUOTA_TTL_S = 3 * 24 * 3600

# Presigned POST policy lifetime, and preview links to originals.
UPLOAD_URL_TTL_S = 300
PREVIEW_URL_TTL_S = 300

# Processing lease: a crashed processor frees the item after this long.
PROCESSING_LEASE_S = 120

MAX_FILES_PER_TRIAL = 24
MAX_BYTES_PER_TRIAL = 120 * MB
MAX_RETRIES = 2

# The Converse API takes at most 3.75 MB and 8000 px per side for each image
# (API reference, Message). Phone originals are 3-8 MB (KOLKATA-FIELD-EVIDENCE.md),
# so anything over a safety margin below that goes as a processing copy.
MAX_BYTES_FOR_BEDROCK = 3_500_000
MAX_PIXELS_SIDE_FOR_BEDROCK = 8000
PROCESSING_COPY_SIDE = 1568

# Decoding is width x height x 3 bytes; 64 MP stays well inside the ingest
# Lambda's 1 GB. A 50 MP phone is 51 MP, the Kolkata phone 9.6 MP.
MAX_PHOTO_PIXELS = 64_000_000

# A QUEUED item that has not been claimed in this long lost its invocation.
QUEUE_STALL_S = 600

# Slack on the presigned POST lifetime, for clock skew between S3 and Lambda.
UPLOAD_WINDOW_SLACK_S = 60

# Textract synchronous AnalyzeDocument: 10 MB, one page.
MAX_BYTES_FOR_TEXTRACT = 10 * MB

GROUPS = {
    "photo": {
        "types": {"image/jpeg": ".jpg", "image/png": ".png"},
        "maxBytes": 15 * MB,
        "maxFiles": 12,
    },
    "slip": {
        "types": {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf"},
        "maxBytes": 10 * MB,
        "maxFiles": 6,
    },
    "bill": {
        "types": {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"},
        "maxBytes": 10 * MB,
        "maxFiles": 2,
    },
    "trace": {
        "types": {"application/json": ".json", "application/geo+json": ".json"},
        "maxBytes": 2 * MB,
        "maxFiles": 4,
    },
}

PHOTO_ROLES = ("before", "after", "current", "additional")

# Trace validation.
TRACE_MIN_POINTS = 2
TRACE_MAX_POINTS = 20_000
TRACE_JUMP_KMH = 150.0


def _int_env(name, default):
    try:
        value = int(os.environ.get(name, ""))
        return value if value >= 0 else default
    except ValueError:
        return default


def daily_trials():
    return _int_env("TRIAL_DAILY_TRIALS", 60)


def daily_bedrock_calls():
    return _int_env("TRIAL_DAILY_BEDROCK_CALLS", 200)


def daily_textract_calls():
    return _int_env("TRIAL_DAILY_TEXTRACT_CALLS", 100)


def max_bedrock_per_trial():
    return _int_env("TRIAL_MAX_BEDROCK_PER_TRIAL", 15)


def max_textract_per_trial():
    return _int_env("TRIAL_MAX_TEXTRACT_PER_TRIAL", 8)


def max_analyses():
    return _int_env("TRIAL_MAX_ANALYSES", 30)


def invite_code():
    return os.environ.get("TRIAL_INVITE_CODE", "")


def processor_function():
    """Name of the Lambda that processes trial evidence (the ingest function)."""
    return os.environ.get("TRIAL_PROCESSOR_FUNCTION", "")


def public_limits():
    """What the client is told, so the UI can check before uploading."""
    return {
        "maxFilesPerTrial": MAX_FILES_PER_TRIAL,
        "maxBytesPerTrial": MAX_BYTES_PER_TRIAL,
        "uploadUrlTtlSeconds": UPLOAD_URL_TTL_S,
        "maxRetries": MAX_RETRIES,
        "maxAnalyses": max_analyses(),
        "maxBedrockCallsPerTrial": max_bedrock_per_trial(),
        "maxTextractCallsPerTrial": max_textract_per_trial(),
        "groups": {
            name: {
                "contentTypes": sorted(spec["types"]),
                "maxBytes": spec["maxBytes"],
                "maxFiles": spec["maxFiles"],
            }
            for name, spec in GROUPS.items()
        },
        "photoRoles": list(PHOTO_ROLES),
    }
