"""HTTP API for the ward engineer's decision screen (plan section 3).

    POST /verify/{billId}         run rules R1-R10, write verdicts
    GET  /bill/{billId}           drains + money summary
    GET  /drain/{drainId}         trips, evidence, routes, failed rules, and
                                  5-minute presigned GET links to its images
    POST /drain/{drainId}/summary Bedrock evidence summary, cached
    POST /decision                hold / approve + note
    POST /upload-url              presigned S3 URL (Day 3)

Verification is deterministic rules only. Every Textract and Bedrock call
except the evidence summary already happened at ingestion, so this stays fast
enough to run on camera.
"""

import datetime
import json
import os
import re
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import bedrock, billdata, config, rules, store  # noqa: E402
from common.jsonlog import log  # noqa: E402

# Presigned PUT links for the live upload are short-lived on purpose.
UPLOAD_URL_TTL_S = 900
UPLOAD_PREFIXES = ("photos", "slips", "traces")

# Presigned GET links to evidence images. Short, because the API has no login:
# anyone holding the drain response can open the images until they expire.
EVIDENCE_URL_TTL_S = 300

# Bill and drain ids are short slugs; anything else is never signed.
_ID = re.compile(r"[A-Za-z0-9_-]{1,32}")

DECISIONS = ("APPROVE", "HOLD")

# Attributes a verification writes onto a drain, so a later decision can move
# money without re-running the rules.
DRAIN_VERDICT_FIELDS = (
    "verdict", "failedRules", "evidenceVerified", "evidenceReview", "evidenceHeld",
    "verifiedTonnes", "reviewTonnes", "heldTonnes", "tripCount", "findings",
)


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-store",
        },
        "body": json.dumps(body, default=str),
    }


def error(status_code, message, **extra):
    return response(status_code, {"error": message, **extra})


# ------------------------------------------------------------------ verify
def verify_bill(bill_id, _body, _query):
    bill_id = bill_id or config.bill_id()
    started = datetime.datetime.now(datetime.timezone.utc)

    context = billdata.load_context(bill_id)

    if context["bill"] is None:
        return error(404, f"No bill {bill_id}. Has the seed run?", billId=bill_id)
    if not context["drains"]:
        return error(409, "The bill has no drains yet.", billId=bill_id)

    gaps = billdata.missing_evidence(context)
    result = rules.evaluate(context)

    # ---- persist the verdicts
    writes = []
    for drain in context["drains"]:
        computed = result["drains"].get(drain["drainId"])
        if computed is None:
            continue
        writes.append(
            {
                **drain,
                **{name: computed[name] for name in DRAIN_VERDICT_FIELDS if name in computed},
            }
        )

    for trip in context["trips"]:
        computed = result["trips"].get(trip.get("tripId"))
        if computed is None:
            continue
        writes.append(
            {
                **trip,
                "verdict": computed["verdict"],
                "failedRules": computed["hardFails"] + computed["softFails"],
                "hardFails": computed["hardFails"],
                "softFails": computed["softFails"],
                "ruleDetail": computed["findings"],
            }
        )

    store.put_many(writes)

    elapsed_ms = round(
        (datetime.datetime.now(datetime.timezone.utc) - started).total_seconds() * 1000
    )
    store.put(
        {
            **context["bill"],
            "status": "VERIFIED",
            "verifiedAt": now_iso(),
            "verificationMs": elapsed_ms,
            "summary": result["summary"],
            "missingEvidence": gaps,
        }
    )

    log(
        "bill_verified",
        billId=bill_id,
        trips=len(result["trips"]),
        drains=len(result["drains"]),
        ms=elapsed_ms,
        **result["summary"],
    )

    return response(
        200,
        {
            "billId": bill_id,
            "verifiedAt": now_iso(),
            "verificationMs": elapsed_ms,
            "tripsChecked": len(result["trips"]),
            "summary": result["summary"],
            "drains": _drain_rows(result["drains"].values()),
            "missingEvidence": gaps,
            "rules": rules.RULE_TEXT,
        },
    )


def _drain_rows(drains):
    rows = []
    for drain in drains:
        rows.append(
            {
                "drainId": drain.get("drainId"),
                "name": drain.get("name"),
                "claimedTonnes": drain.get("claimedTonnes"),
                "verifiedTonnes": drain.get("verifiedTonnes"),
                "reviewTonnes": drain.get("reviewTonnes"),
                "heldTonnes": drain.get("heldTonnes"),
                "verdict": drain.get("verdict"),
                "decision": drain.get("decision"),
                "note": drain.get("note"),
                "tripCount": drain.get("tripCount"),
                "failedRules": drain.get("failedRules") or [],
            }
        )
    rows.sort(key=lambda row: int(row["drainId"]) if str(row["drainId"]).isdigit() else 0)
    return rows


# -------------------------------------------------------------------- bill
def get_bill(bill_id, _body, _query):
    bill_id = bill_id or config.bill_id()
    rows = store.query_pk(store.bill_pk(bill_id))

    bill = next((row for row in rows if row["sk"] == "META"), None)
    if bill is None:
        return error(404, f"No bill {bill_id}. Has the seed run?", billId=bill_id)

    drains = [row for row in rows if row["sk"].startswith("DRAIN#")]
    verified = bill.get("status") == "VERIFIED"

    for drain in drains:
        if not verified:
            # Before a verification run, nothing is verified or held: the whole
            # claim is simply pending.
            drain["verifiedTonnes"] = 0
            drain["reviewTonnes"] = 0
            drain["heldTonnes"] = 0
        else:
            decided = rules.apply_decision(
                drain.get("decision"),
                drain.get("evidenceVerified") or 0,
                drain.get("evidenceReview") or 0,
                drain.get("evidenceHeld") or 0,
            )
            drain["verifiedTonnes"], drain["reviewTonnes"], drain["heldTonnes"] = decided

    summary = rules.summarise(drains, bill.get("ratePerTonne", config.RATE_PER_TONNE))
    summary["claimedTonnes"] = bill.get("claimedTonnes", summary["claimedTonnes"])
    summary["claimedRupees"] = int(
        round(summary["claimedTonnes"] * summary["ratePerTonne"])
    )
    if not verified:
        summary["pendingTonnes"] = summary["claimedTonnes"]

    return response(
        200,
        {
            "billId": bill_id,
            "contractor": bill.get("contractor"),
            "ward": bill.get("ward"),
            "status": bill.get("status", "PENDING"),
            "verifiedAt": bill.get("verifiedAt"),
            "verificationMs": bill.get("verificationMs"),
            "workWindow": [bill.get("workWindowStart"), bill.get("workWindowEnd")],
            "simulated": bill.get("simulated", True),
            "summary": summary,
            "drains": _drain_rows(drains),
            "missingEvidence": bill.get("missingEvidence"),
            "rules": rules.RULE_TEXT,
        },
    )


# ------------------------------------------------------------------- drain
def get_drain(drain_id, _body, query):
    bill_id = (query or {}).get("billId") or config.bill_id()

    drain = store.get(store.bill_pk(bill_id), store.drain_sk(drain_id))
    if drain is None:
        return error(404, f"No drain {drain_id} on bill {bill_id}", drainId=drain_id)

    trips = [
        row
        for row in store.query_pk(store.bill_pk(bill_id), f"TRIP#{drain_id}#")
        if row.get("drainId") == drain_id
    ]
    trips.sort(key=lambda trip: trip.get("tripNo") or "")

    dumpsite = store.get(store.dumpsite_pk("D1"), "META")
    bucket = config.evidence_bucket()

    trace_keys = [trip.get("traceKey") for trip in trips if trip.get("traceKey")]
    traces, trace_problems = billdata.load_traces(bucket, trace_keys)

    slip_items = store.batch_get(
        [(store.evidence_pk(trip["slipKey"]), "SLIP") for trip in trips if trip.get("slipKey")]
    )
    slips = {
        item["s3Key"]: item for item in slip_items.values() if item.get("s3Key")
    }

    photos = [
        item
        for item in store.scan_sk("PHOTO", billId=bill_id)
        if item.get("drainId") == drain_id
    ]
    photos.sort(key=lambda item: (item.get("role") or "", item.get("s3Key") or ""))

    claimed = billdata.claimed_route(drain, dumpsite)

    trip_rows = []
    for trip in trips:
        points = traces.get(trip.get("traceKey")) or []
        trip_rows.append(
            {
                "tripId": trip.get("tripId"),
                "tripNo": trip.get("tripNo"),
                "vehicleNo": trip.get("vehicleNo"),
                "claimedTonnes": trip.get("claimedTonnes"),
                "verdict": trip.get("verdict"),
                "hardFails": trip.get("hardFails") or [],
                "softFails": trip.get("softFails") or [],
                "findings": trip.get("ruleDetail") or [],
                "startTime": trip.get("startTime"),
                "arrivalTime": trip.get("arrivalTime"),
                "slipKey": trip.get("slipKey"),
                "traceKey": trip.get("traceKey"),
                "slip": _slip_row(slips.get(trip.get("slipKey"))),
                "slipImageUrl": evidence_url(
                    slips.get(trip.get("slipKey")), "slip", bill_id, drain_id
                ),
                "actualRoute": [
                    [point["lon"], point["lat"]] for point in billdata.downsample(points)
                ],
                "actualRouteDistanceM": billdata.route_distance_m(points),
                "tracePointCount": len(points),
                "traceProblem": trace_problems.get(trip.get("traceKey")),
            }
        )

    return response(
        200,
        {
            "billId": bill_id,
            "drainId": drain_id,
            "name": drain.get("name"),
            "verdict": drain.get("verdict"),
            "decision": drain.get("decision"),
            "note": drain.get("note"),
            "decidedAt": drain.get("decidedAt"),
            "claimedTonnes": drain.get("claimedTonnes"),
            "verifiedTonnes": drain.get("verifiedTonnes"),
            "reviewTonnes": drain.get("reviewTonnes"),
            "heldTonnes": drain.get("heldTonnes"),
            "lengthM": drain.get("lengthM"),
            "widthM": drain.get("widthM"),
            "depthM": drain.get("depthM"),
            "plausibleMaxTonnes": drain.get("plausibleMaxTonnes"),
            "geofence": drain.get("geofence"),
            "claimedRoute": claimed,
            "dumpsite": {
                "name": (dumpsite or {}).get("name"),
                "center": (dumpsite or {}).get("center"),
                "geofence": (dumpsite or {}).get("geofence"),
            },
            "findings": drain.get("findings") or [],
            "failedRules": drain.get("failedRules") or [],
            "summary": drain.get("summary"),
            "summaryModelId": drain.get("summaryModelId"),
            "photos": [_photo_row(item, bill_id, drain_id) for item in photos],
            "trips": trip_rows,
            "evidenceUrlExpiresInSeconds": EVIDENCE_URL_TTL_S,
            "rules": rules.RULE_TEXT,
        },
    )


def evidence_key_ok(key, kind, bill_id, drain_id):
    """True only for the canonical key of this bill's and this drain's image.

    The keys come from DynamoDB, not the request, but they are still checked
    against the exact layout the ingest Lambda accepts, so a bad item can
    never get a link to another bill's evidence, a trace, or an arbitrary
    object: no '..', no extra '/', no other prefix.
    """
    if not isinstance(key, str) or not _ID.fullmatch(str(bill_id or "")) \
            or not _ID.fullmatch(str(drain_id or "")):
        return False
    bill, drain = re.escape(bill_id), re.escape(drain_id)
    if kind == "photo":
        pattern = rf"photos/{bill}/drain{drain}/(before|after|load)-[A-Za-z0-9_-]{{1,32}}\.(jpe?g|png)"
    elif kind == "slip":
        pattern = rf"slips/{bill}/{drain}-[0-9]{{3}}\.(png|jpe?g)"
    else:
        return False
    return re.fullmatch(pattern, key) is not None


def evidence_url(item, kind, bill_id, drain_id):
    """A 5-minute presigned GET link to one evidence image, or None.

    None when there is no bucket, no evidence item, or the item does not
    belong to this bill and drain. The link itself is never logged or stored.
    """
    bucket = config.evidence_bucket()
    if not bucket or not item:
        return None

    key = item.get("s3Key")
    if item.get("billId") not in (None, bill_id) or item.get("drainId") not in (None, drain_id):
        log("evidence_url_refused", kind=kind, reason="owner_mismatch", drainId=drain_id)
        return None
    if not evidence_key_ok(key, kind, bill_id, drain_id):
        log("evidence_url_refused", kind=kind, reason="bad_key", drainId=drain_id)
        return None

    try:
        from common import awsclients

        return awsclients.client("s3").generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=EVIDENCE_URL_TTL_S,
        )
    except Exception as exc:
        log("evidence_url_failed", kind=kind, error=type(exc).__name__)
        return None


def _photo_row(item, bill_id=None, drain_id=None):
    verdict = item.get("bedrock") or {}
    return {
        "s3Key": item.get("s3Key"),
        "imageUrl": evidence_url(item, "photo", bill_id, drain_id),
        "role": item.get("role"),
        "status": item.get("status"),
        "lat": item.get("lat"),
        "lon": item.get("lon"),
        "timestamp": item.get("timestamp"),
        "hasGps": item.get("hasGps"),
        "pHash": item.get("pHash"),
        "problems": item.get("problems") or [],
        "bedrock": {
            "cleared": verdict.get("cleared"),
            "loadType": verdict.get("load_type"),
            "confidence": verdict.get("confidence"),
            "notes": verdict.get("notes"),
            "modelId": verdict.get("modelId"),
            "mocked": bool(verdict.get("mocked")),
            "ok": verdict.get("ok"),
        },
    }


def _slip_row(item):
    if item is None:
        return None

    textract = item.get("textract") or {}
    return {
        "s3Key": item.get("s3Key"),
        "status": item.get("status"),
        "ticketNo": item.get("ticketNo"),
        "vehicleNo": item.get("vehicleNo"),
        "gross": item.get("gross"),
        "tare": item.get("tare"),
        "net": item.get("net"),
        "timeIn": item.get("timeIn"),
        "timeOut": item.get("timeOut"),
        "site": item.get("site"),
        "fields": textract.get("fields"),
        "confidenceAvg": textract.get("confidenceAvg"),
        "missingFields": textract.get("missingFields") or [],
        "lowConfidenceFields": textract.get("lowConfidenceFields") or [],
    }


# ----------------------------------------------------------------- summary
SUMMARY_PROMPT = """You are helping a municipal ward engineer decide whether to pay part of a \
drain desilting bill.

Drain {drain_id} ({name}) claims {claimed} tonnes of silt removed.
Automated checks produced these findings:

{findings}

Write exactly two sentences for the engineer, in plain English, saying what the \
evidence shows and why the money is being held or queried. State only what the \
findings support. Do not add a recommendation, a greeting or a heading."""


def drain_summary(drain_id, body, query):
    bill_id = (body or {}).get("billId") or (query or {}).get("billId") or config.bill_id()
    refresh = bool((body or {}).get("refresh")) or (query or {}).get("refresh") == "1"

    drain = store.get(store.bill_pk(bill_id), store.drain_sk(drain_id))
    if drain is None:
        return error(404, f"No drain {drain_id} on bill {bill_id}", drainId=drain_id)

    if drain.get("summary") and not refresh:
        return response(
            200,
            {
                "drainId": drain_id,
                "summary": drain["summary"],
                "modelId": drain.get("summaryModelId"),
                "cached": True,
            },
        )

    findings = list(drain.get("findings") or [])
    for trip in store.query_pk(store.bill_pk(bill_id), f"TRIP#{drain_id}#"):
        findings.extend(trip.get("ruleDetail") or [])

    if not findings:
        if drain.get("verdict") is None:
            return error(409, "Run verification before asking for a summary.",
                         drainId=drain_id)
        text = "Every check passed for this drain. No evidence was found against the claim."
        store.update_fields(
            store.bill_pk(bill_id),
            store.drain_sk(drain_id),
            {"summary": text, "summaryModelId": None, "summaryAt": now_iso()},
        )
        return response(
            200, {"drainId": drain_id, "summary": text, "modelId": None, "cached": False}
        )

    # One line per distinct rule, so the prompt does not repeat itself 18 times.
    unique = {}
    for item in findings:
        unique.setdefault(item["rule"], item)

    lines = "\n".join(
        f"- {item['rule']} ({item['severity']}): {item['message']}" for item in unique.values()
    )

    prompt = SUMMARY_PROMPT.format(
        drain_id=drain_id,
        name=drain.get("name") or "unnamed section",
        claimed=drain.get("claimedTonnes"),
        findings=lines,
    )

    try:
        raw = bedrock.summarise(prompt)
        text = bedrock.response_text(raw).strip()
    except Exception as exc:
        log("summary_failed", drainId=drain_id, error=f"{type(exc).__name__}: {exc}")
        return error(502, "The evidence summary could not be generated.",
                     drainId=drain_id, detail=f"{type(exc).__name__}")

    if not text:
        return error(502, "The model returned an empty summary.", drainId=drain_id)

    # Mock mode builds the text from the findings; naming a model would claim
    # Bedrock wrote it.
    model_id = None if config.mock_aws() else config.text_model_id()
    store.update_fields(
        store.bill_pk(bill_id),
        store.drain_sk(drain_id),
        {"summary": text, "summaryModelId": model_id, "summaryAt": now_iso()},
    )

    return response(
        200,
        {
            "drainId": drain_id,
            "summary": text,
            "modelId": model_id,
            "cached": False,
            "basedOnRules": sorted(unique),
        },
    )


# ---------------------------------------------------------------- decision
def post_decision(_path_param, body, _query):
    body = body or {}
    bill_id = body.get("billId") or config.bill_id()
    drain_id = body.get("drainId")
    decision = (body.get("decision") or "").upper()
    note = body.get("note")

    if not drain_id:
        return error(400, "drainId is required")
    if decision not in DECISIONS:
        return error(400, f"decision must be one of {', '.join(DECISIONS)}",
                     received=body.get("decision"))
    if note is not None and not isinstance(note, str):
        return error(400, "note must be text")

    drain = store.get(store.bill_pk(bill_id), store.drain_sk(drain_id))
    if drain is None:
        return error(404, f"No drain {drain_id} on bill {bill_id}", drainId=drain_id)
    if drain.get("verdict") is None:
        return error(409, "Run verification before deciding.", drainId=drain_id)

    verified, review, held = rules.apply_decision(
        decision,
        drain.get("evidenceVerified") or 0,
        drain.get("evidenceReview") or 0,
        drain.get("evidenceHeld") or 0,
    )

    updated = store.update_fields(
        store.bill_pk(bill_id),
        store.drain_sk(drain_id),
        {
            "decision": decision,
            "note": (note or "").strip()[:500] or None,
            "decidedAt": now_iso(),
            "decidedBy": "ward-engineer",
            "verifiedTonnes": verified,
            "reviewTonnes": review,
            "heldTonnes": held,
        },
    )

    rows = store.query_pk(store.bill_pk(bill_id))
    bill = next((row for row in rows if row["sk"] == "META"), None)
    drains = [row for row in rows if row["sk"].startswith("DRAIN#")]

    for row in drains:
        row["verifiedTonnes"], row["reviewTonnes"], row["heldTonnes"] = rules.apply_decision(
            row.get("decision"),
            row.get("evidenceVerified") or 0,
            row.get("evidenceReview") or 0,
            row.get("evidenceHeld") or 0,
        )

    summary = rules.summarise(drains, (bill or {}).get("ratePerTonne", config.RATE_PER_TONNE))
    summary["claimedTonnes"] = (bill or {}).get("claimedTonnes", summary["claimedTonnes"])
    summary["claimedRupees"] = int(round(summary["claimedTonnes"] * summary["ratePerTonne"]))

    if bill is not None:
        store.put({**bill, "summary": summary})

    log("decision_recorded", billId=bill_id, drainId=drain_id, decision=decision,
        hasNote=bool(note))

    return response(
        200,
        {
            "billId": bill_id,
            "drainId": drain_id,
            "decision": decision,
            "note": updated.get("note"),
            "decidedAt": updated.get("decidedAt"),
            "drain": {
                "drainId": drain_id,
                "verdict": updated.get("verdict"),
                "verifiedTonnes": verified,
                "reviewTonnes": review,
                "heldTonnes": held,
            },
            "summary": summary,
            "drains": _drain_rows(drains),
            "saved": True,
        },
    )


# --------------------------------------------------------------- uploading
def post_upload_url(_path_param, body, _query):
    """A presigned PUT for the live demo upload (plan section 7, Day 3)."""
    body = body or {}
    prefix = (body.get("prefix") or "photos").strip("/")
    filename = (body.get("filename") or "").strip()
    bucket = config.evidence_bucket()

    if prefix not in UPLOAD_PREFIXES:
        return error(400, f"prefix must be one of {', '.join(UPLOAD_PREFIXES)}",
                     received=body.get("prefix"))
    if not bucket:
        return error(503, "No evidence bucket is configured.")

    suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ".jpg"
    if len(suffix) > 6 or not suffix[1:].isalnum():
        suffix = ".jpg"

    # Live uploads land under a live/ folder so reset.py can find and remove
    # them without touching the seeded evidence.
    key = f"{prefix}/live/{uuid.uuid4().hex}{suffix}"
    content_type = body.get("contentType") or (
        "application/json" if prefix == "traces" else "image/jpeg"
    )

    try:
        from common import awsclients

        url = awsclients.client("s3").generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=UPLOAD_URL_TTL_S,
        )
    except Exception as exc:
        log("presign_failed", error=f"{type(exc).__name__}: {exc}")
        return error(502, "Could not create an upload URL.")

    return response(
        200,
        {
            "uploadUrl": url,
            "bucket": bucket,
            "key": key,
            "contentType": content_type,
            "expiresInSeconds": UPLOAD_URL_TTL_S,
        },
    )


# ------------------------------------------------------------------ health
def get_health(_path_param, _body, _query):
    return response(
        200,
        {
            "service": "siltproof-api",
            "status": "ok",
            "table": config.table_name(),
            "bucket": config.evidence_bucket(),
            "bedrockModelId": config.text_model_id(),
            "bedrockVisionModelId": config.vision_model_id(),
            "mockAws": config.mock_aws(),
        },
    )


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
        return error(404, f"No route for {route_key or 'request'}")

    handler, param_name = route
    path_params = event.get("pathParameters") or {}
    param = path_params.get(param_name) if param_name else None

    try:
        body = json.loads(event["body"]) if event.get("body") else {}
    except json.JSONDecodeError:
        return error(400, "Body is not valid JSON")

    if not isinstance(body, dict):
        return error(400, "Body must be a JSON object")

    query = event.get("queryStringParameters") or {}

    try:
        return handler(param, body, query)
    except Exception as exc:
        log("request_failed", routeKey=route_key, error=f"{type(exc).__name__}: {exc}")
        return error(500, "The request failed.", detail=f"{type(exc).__name__}")
