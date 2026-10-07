"""HTTP API for the ward engineer's decision screen.

Stub for Day 1 morning: every route returns hard-coded placeholder JSON in the
shape the frontend will consume. Nothing reads DynamoDB or calls Bedrock yet.

Routes (plan section 3):
  POST /verify/{billId}        run rules R1-R10, write verdicts
  GET  /bill/{billId}          drains + money summary
  GET  /drain/{drainId}        trips, evidence, verdicts
  POST /drain/{drainId}/summary Bedrock 2-line evidence summary
  POST /decision               hold / approve + note
  POST /upload-url             presigned S3 PUT URL (live demo)
"""

import json
import os

TABLE_NAME = os.environ.get("TABLE_NAME", "siltproof")
EVIDENCE_BUCKET = os.environ.get("EVIDENCE_BUCKET", "")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "")

RATE_PER_TONNE = 1800

# Placeholder verdicts for the planted cases (plan section 6). Everything else
# is green until the real rules run.
PLANTED = {
    3: "HOLD",
    6: "REVIEW",
    8: "REVIEW",
    11: "HOLD",
    14: "HOLD",
    16: "HOLD",
}


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }


def placeholder_drains():
    drains = []
    for n in range(1, 19):
        drains.append(
            {
                "drainId": str(n),
                "name": f"Drain section {n}",
                "claimedTonnes": 70,
                "verdict": PLANTED.get(n, "VERIFIED"),
                "decision": None,
            }
        )
    return drains


def placeholder_summary():
    claimed = 1240
    verified = 870
    held = claimed - verified
    return {
        "ratePerTonne": RATE_PER_TONNE,
        "claimedTonnes": claimed,
        "verifiedTonnes": verified,
        "heldTonnes": held,
        "claimedRupees": claimed * RATE_PER_TONNE,
        "verifiedRupees": verified * RATE_PER_TONNE,
        "heldRupees": held * RATE_PER_TONNE,
    }


# ------------------------------------------------------------------ routes
def get_bill(bill_id, body):
    # TODO Day 2: read BILL#<id> META + DRAIN# items from DynamoDB.
    return response(
        200,
        {
            "billId": bill_id,
            "contractor": "Placeholder Contractor",
            "ward": "Placeholder ward",
            "status": "PENDING",
            "summary": placeholder_summary(),
            "drains": placeholder_drains(),
        },
    )


def verify_bill(bill_id, body):
    # TODO Day 2: run rules R1-R10 and write trip + drain verdicts.
    return response(
        200,
        {
            "billId": bill_id,
            "verifiedAt": None,
            "tripsChecked": 0,
            "summary": placeholder_summary(),
            "drains": placeholder_drains(),
        },
    )


def get_drain(drain_id, body):
    # TODO Day 2: read DRAIN# + TRIP# + EVID# items for this drain.
    return response(
        200,
        {
            "drainId": drain_id,
            "name": f"Drain section {drain_id}",
            "verdict": PLANTED.get(int(drain_id), "VERIFIED") if drain_id.isdigit() else "VERIFIED",
            "claimedTonnes": 70,
            "decision": None,
            "note": None,
            "trips": [],
            "evidence": {"photos": [], "slips": [], "traces": []},
            "failedRules": [],
        },
    )


def drain_summary(drain_id, body):
    # TODO Day 2: Bedrock text call over the failed-rule JSON, cached.
    return response(
        200,
        {
            "drainId": drain_id,
            "summary": "Placeholder evidence summary. Bedrock writes this on Day 2.",
            "modelId": BEDROCK_MODEL_ID,
            "cached": False,
        },
    )


def post_decision(_path_param, body):
    # TODO Day 2: write the engineer's decision onto the drain item.
    return response(
        200,
        {
            "billId": body.get("billId"),
            "drainId": body.get("drainId"),
            "decision": body.get("decision"),
            "note": body.get("note"),
            "saved": False,
        },
    )


def post_upload_url(_path_param, body):
    # TODO Day 3: boto3 generate_presigned_url('put_object') on the prefix.
    return response(
        200,
        {
            "uploadUrl": None,
            "bucket": EVIDENCE_BUCKET,
            "key": None,
            "requested": {
                "prefix": body.get("prefix"),
                "filename": body.get("filename"),
            },
        },
    )


def get_health(_path_param, _body):
    return response(
        200,
        {
            "service": "siltproof-api",
            "status": "ok",
            "table": TABLE_NAME,
            "bucket": EVIDENCE_BUCKET,
            "bedrockModelId": BEDROCK_MODEL_ID,
        },
    )


# routeKey -> (handler, name of the path parameter it needs)
ROUTES = {
    "GET /health": (get_health, None),
    "POST /verify/{billId}": (verify_bill, "billId"),
    "GET /bill/{billId}": (get_bill, "billId"),
    "GET /drain/{drainId}": (get_drain, "drainId"),
    "POST /drain/{drainId}/summary": (drain_summary, "drainId"),
    "POST /decision": (post_decision, None),
    "POST /upload-url": (post_upload_url, None),
}


def lambda_handler(event, context):
    route_key = event.get("routeKey", "")
    route = ROUTES.get(route_key)

    if route is None:
        return response(404, {"message": f"No route for {route_key or 'request'}"})

    handler, param_name = route
    path_params = event.get("pathParameters") or {}
    param = path_params.get(param_name) if param_name else None

    try:
        body = json.loads(event["body"]) if event.get("body") else {}
    except json.JSONDecodeError:
        return response(400, {"message": "Body is not valid JSON"})

    return handler(param, body)
