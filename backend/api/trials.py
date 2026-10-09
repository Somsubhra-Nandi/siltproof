"""Judge trial routes (docs/JUDGE-TRIAL-API.md).

Kept out of api/app.py so the B1 routes are untouched: app.py forwards any
route key listed in ROUTES here and does nothing else for trials.

Every route under /trials/{trialId} goes through ``authorise`` first, which
checks the ID's shape, the bearer token against its stored hash, and expiry.
A wrong token and an unknown trial get the same 404.
"""

import datetime
import hmac
import json
import re

from botocore.exceptions import ClientError

from common import awsclients, config
from common.jsonlog import log
from trial import analysis, ids, limits, process, repo, validate

KINDS = ("judge", "field")

PRIVACY_NOTICE = (
    "Files you upload are stored in a private Amazon S3 bucket and read by Amazon "
    "Textract (weighbridge slips) and Amazon Bedrock running Amazon Nova Pro (photos). "
    "Trial records are kept in Amazon DynamoDB. Everything is set to be deleted about "
    "48 hours after the trial is created (S3 and DynamoDB delete asynchronously, so it "
    "can take longer), or straight away when you delete the trial. Photo EXIF can reveal "
    "exactly where and when a photo was taken. Do not upload confidential, personal or "
    "sensitive records."
)


# ------------------------------------------------------------------ plumbing
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


def error(status_code, code, message, **extra):
    return response(status_code, {"error": message, "code": code, **extra})


class Refused(Exception):
    def __init__(self, reply):
        super().__init__("refused")
        self.reply = reply


def bearer(event):
    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
    value = headers.get("authorization") or ""
    match = re.fullmatch(r"\s*Bearer\s+(\S+)\s*", value)
    return match.group(1) if match else None


def authorise(event, trial_id):
    """The trial, or raise Refused with a 404/410 reply."""
    not_found = error(404, "TRIAL_NOT_FOUND", "No such trial, or the access token does not match.")
    if not ids.valid_trial_id(trial_id):
        raise Refused(not_found)
    token = bearer(event)
    trial = repo.get_trial(trial_id)
    if trial is None or not ids.token_matches(token, trial.get("tokenHash")):
        raise Refused(not_found)
    if int(trial.get("ttl") or 0) <= repo.now_epoch():
        raise Refused(error(410, "TRIAL_EXPIRED",
                            "This trial has expired. Its evidence is being deleted."))
    return trial


def evidence_or_404(trial_id, evidence_id):
    if not ids.valid_evidence_id(evidence_id):
        raise Refused(error(404, "EVIDENCE_NOT_FOUND", "No such evidence in this trial."))
    item = repo.get_evidence(trial_id, evidence_id)
    if item is None:
        raise Refused(error(404, "EVIDENCE_NOT_FOUND", "No such evidence in this trial."))
    return item


def s3():
    return awsclients.client("s3")


def bucket_or_refuse():
    bucket = config.evidence_bucket()
    if not bucket:
        raise Refused(error(503, "STORAGE_UNAVAILABLE", "No evidence bucket is configured."))
    return bucket


# ---------------------------------------------------------------- views
_PREVIEWABLE = {"image/jpeg", "image/png", "application/pdf"}


def preview_url(trial_id, item):
    key = item.get("s3Key") or ""
    pattern = rf"trials/{re.escape(trial_id)}/originals/{re.escape(item['evidenceId'])}\.(jpg|png|pdf)"
    if item.get("contentType") not in _PREVIEWABLE or not re.fullmatch(pattern, key):
        return None
    if item.get("state") in ("UPLOADING", "REJECTED"):
        return None
    try:
        return s3().generate_presigned_url(
            "get_object",
            Params={"Bucket": config.evidence_bucket(), "Key": key,
                    "ResponseContentType": item["contentType"]},
            ExpiresIn=limits.PREVIEW_URL_TTL_S,
        )
    except Exception as exc:
        log("trial_preview_failed", error=type(exc).__name__)
        return None


EVIDENCE_FIELDS = (
    "evidenceId", "group", "role", "filename", "contentType", "declaredSizeBytes",
    "sizeBytes", "sha256", "state", "error", "attempts", "createdAt", "updatedAt",
    "processedAt", "result",
)


def evidence_view(trial_id, item, with_preview=True):
    view = {name: item.get(name) for name in EVIDENCE_FIELDS}
    if with_preview:
        view["previewUrl"] = preview_url(trial_id, item)
        view["previewExpiresInSeconds"] = limits.PREVIEW_URL_TTL_S
    view["retryable"] = retryable(item)
    return view


def trial_view(trial, evidence, with_preview=True):
    return {
        "trialId": trial["trialId"],
        "kind": trial.get("kind"),
        "label": trial.get("label"),
        "status": trial.get("status"),
        "createdAt": trial.get("createdAt"),
        "expiresAt": trial.get("expiresAt"),
        "details": trial.get("details") or {},
        "usage": {
            "files": trial.get("fileCount", 0),
            "bytesReserved": trial.get("bytesReserved", 0),
            "bedrockCalls": trial.get("bedrockCalls", 0),
            "textractCalls": trial.get("textractCalls", 0),
            "analyses": trial.get("analysisCount", 0),
        },
        "limits": limits.public_limits(),
        "evidence": [evidence_view(trial["trialId"], item, with_preview) for item in evidence],
        "hasResults": bool(trial.get("lastAnalyzedAt")),
        "lastAnalyzedAt": trial.get("lastAnalyzedAt"),
        "mockAws": config.mock_aws(),
        "privacy": privacy(),
    }


def privacy():
    return {
        "retentionHours": limits.TRIAL_LIFETIME_S // 3600,
        "services": ["Amazon S3", "Amazon DynamoDB", "Amazon Textract",
                     "Amazon Bedrock (Amazon Nova Pro)"],
        "notice": PRIVACY_NOTICE,
    }


NOT_RETRYABLE = {"UNREADABLE_IMAGE", "PDF_TOO_MANY_PAGES", "FILE_TOO_LARGE",
                 "IMAGE_TOO_LARGE", "CHANGED_AFTER_UPLOAD"}


def upload_window_closed(item):
    """The presigned POST for this item can no longer be used.

    Until then the browser can still write to the key, so a file slot given
    back earlier would let deleted or rejected files be replaced by uploads
    that no counter sees.
    """
    try:
        created = datetime.datetime.fromisoformat(item["createdAt"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return False
    return created + limits.UPLOAD_URL_TTL_S + limits.UPLOAD_WINDOW_SLACK_S < repo.now_epoch()


def release_if_closed(trial_id, item):
    """Give the slot back only when the upload link is dead. Returns whether it was."""
    if not upload_window_closed(item):
        return False
    repo.release_file(trial_id, item["group"], item["declaredSizeBytes"])
    return True


def retryable(item):
    if (item.get("attempts") or 0) >= limits.MAX_RETRIES + 1:
        return False
    if item.get("group") in ("photo", "slip") and process.stalled(item):
        return True
    code = (item.get("error") or {}).get("code")
    if item.get("state") == "FAILED":
        return code not in NOT_RETRYABLE
    if item.get("state") == "READY" and item.get("group") == "photo":
        vision = ((item.get("result") or {}).get("vision")) or {}
        return not vision.get("ran")
    return False


# --------------------------------------------------------------- handlers
def create_trial(event, params, body):
    expected = limits.invite_code()
    if not expected and not config.mock_aws():
        # Fail closed: a deployment without an invite code never opens trials
        # (and their Bedrock and Textract allowances) to the whole internet.
        return error(503, "TRIALS_DISABLED",
                     "Trials are not open on this deployment: no invite code is configured.")
    if expected:
        given = body.get("inviteCode")
        if not isinstance(given, str) or not hmac.compare_digest(
                given.strip().encode("utf-8"), expected.encode("utf-8")):
            return error(403, "INVITE_REQUIRED", "A valid invite code is needed to start a trial.")

    kind = body.get("kind") or "judge"
    if kind not in KINDS:
        return error(400, "INVALID_REQUEST", f"kind must be one of {', '.join(KINDS)}")
    try:
        label = validate.clean_text(body.get("label"), 80)
    except ValueError as exc:
        return error(400, "INVALID_REQUEST", f"label {exc}")

    try:
        repo.reserve_daily("TRIALS", limits.daily_trials())
    except repo.LimitReached:
        return error(429, "TRIAL_QUOTA_EXHAUSTED",
                     "Today's allowance of new trials is used up. Try again tomorrow (UTC).")

    trial_id = ids.new_trial_id()
    token = ids.new_token()
    now = datetime.datetime.now(datetime.timezone.utc)
    expires = now + datetime.timedelta(seconds=limits.TRIAL_LIFETIME_S)
    item = {
        "pk": repo.trial_pk(trial_id),
        "sk": repo.META,
        "trialId": trial_id,
        "kind": kind,
        "label": label,
        "status": "CREATED",
        "tokenHash": ids.hash_token(token),
        "createdAt": now.isoformat(),
        "expiresAt": expires.isoformat(),
        "ttl": int(expires.timestamp()),
        "details": {},
        "fileCount": 0,
        "bytesReserved": 0,
        "groupFiles": {name: 0 for name in limits.GROUPS},
        "bedrockCalls": 0,
        "textractCalls": 0,
        "analysisCount": 0,
    }
    repo.create_trial(item)
    log("trial_created", trialId=trial_id, kind=kind)

    return response(201, {
        "trialId": trial_id,
        "accessToken": token,
        "kind": kind,
        "label": label,
        "createdAt": item["createdAt"],
        "expiresAt": item["expiresAt"],
        "limits": limits.public_limits(),
        "privacy": privacy(),
    })


def get_trial(event, params, body):
    trial = authorise(event, params.get("trialId"))
    evidence = repo.list_evidence(trial["trialId"])
    return response(200, trial_view(trial, evidence))


def put_details(event, params, body):
    trial = authorise(event, params.get("trialId"))
    updates, clears, errors = validate.details(body)
    if errors:
        return error(400, "INVALID_DETAILS", "Some details are not valid.", fields=errors)
    details = {**(trial.get("details") or {}), **updates}
    for name in clears:
        details.pop(name, None)
    trial = repo.update_trial(trial["trialId"], {"details": details, "updatedAt": repo.now_iso()})
    log("trial_details_updated", trialId=trial["trialId"], set=sorted(updates), cleared=clears)
    return response(200, {"trialId": trial["trialId"], "details": trial.get("details") or {}})


def upload_url(event, params, body):
    trial = authorise(event, params.get("trialId"))
    trial_id = trial["trialId"]
    bucket = bucket_or_refuse()

    request, code, message = validate.upload_request(body)
    if request is None:
        return error(413 if code == "FILE_TOO_LARGE" else 400, code, message)

    try:
        repo.reserve_file(trial_id, request["group"], request["sizeBytes"])
    except repo.LimitReached:
        fresh = repo.get_trial(trial_id) or trial
        group_max = limits.GROUPS[request["group"]]["maxFiles"]
        if (fresh.get("groupFiles") or {}).get(request["group"], 0) >= group_max:
            return error(413, "TRIAL_FILE_LIMIT",
                         f"A trial can hold at most {group_max} {request['group']} files.")
        if (fresh.get("fileCount") or 0) >= limits.MAX_FILES_PER_TRIAL:
            return error(413, "TRIAL_FILE_LIMIT",
                         f"A trial can hold at most {limits.MAX_FILES_PER_TRIAL} files.")
        return error(413, "TRIAL_BYTES_LIMIT",
                     f"A trial can hold at most {limits.MAX_BYTES_PER_TRIAL // limits.MB} MB.")

    evidence_id = ids.new_evidence_id()
    key = process.original_key(trial_id, evidence_id, request["extension"])
    now = repo.now_iso()
    item = {
        "evidenceId": evidence_id,
        "trialId": trial_id,
        "group": request["group"],
        "role": request["role"],
        "filename": request["filename"],
        "contentType": request["contentType"],
        "declaredSizeBytes": request["sizeBytes"],
        "s3Key": key,
        "state": "UPLOADING",
        "attempts": 0,
        "createdAt": now,
        "updatedAt": now,
        "ttl": trial["ttl"],
    }
    repo.put_evidence(trial_id, item)

    try:
        post = s3().generate_presigned_post(
            Bucket=bucket,
            Key=key,
            Fields={"Content-Type": request["contentType"]},
            Conditions=[
                {"Content-Type": request["contentType"]},
                ["content-length-range", 1, request["sizeBytes"]],
            ],
            ExpiresIn=limits.UPLOAD_URL_TTL_S,
        )
    except Exception as exc:
        log("trial_presign_failed", trialId=trial_id, error=type(exc).__name__)
        repo.delete_evidence_item(trial_id, evidence_id)
        repo.release_file(trial_id, request["group"], request["sizeBytes"])
        return error(502, "STORAGE_UNAVAILABLE", "Could not create an upload link.")

    log("trial_upload_url", trialId=trial_id, evidenceId=evidence_id, group=request["group"],
        size=request["sizeBytes"])
    return response(201, {
        "evidenceId": evidence_id,
        "upload": {
            "method": "POST",
            "url": post["url"],
            "fields": post["fields"],
            "expiresInSeconds": limits.UPLOAD_URL_TTL_S,
            "maxBytes": request["sizeBytes"],
        },
        "evidence": evidence_view(trial_id, item, with_preview=False),
    })


def _reject(trial_id, item, code, message):
    try:
        s3().delete_object(Bucket=config.evidence_bucket(), Key=item["s3Key"])
    except Exception as exc:
        log("trial_reject_delete_failed", error=type(exc).__name__)
    updated = repo.update_evidence(
        trial_id, item["evidenceId"],
        {"state": "REJECTED", "error": {"code": code, "message": message},
         "updatedAt": repo.now_iso()},
        expect_states=("UPLOADING", "UPLOADED"),
    )
    release_if_closed(trial_id, item)
    log("trial_evidence_rejected", trialId=trial_id, evidenceId=item["evidenceId"], code=code)
    return error(422, code, message, evidence=evidence_view(trial_id, updated, False))


def complete(event, params, body):
    trial = authorise(event, params.get("trialId"))
    trial_id = trial["trialId"]
    item = evidence_or_404(trial_id, params.get("evidenceId"))
    bucket = bucket_or_refuse()

    if item["state"] not in ("UPLOADING", "UPLOADED"):
        # Calling complete twice is harmless: report where the item is.
        return response(200, {"evidence": evidence_view(trial_id, item)})

    try:
        head = s3().head_object(Bucket=bucket, Key=item["s3Key"])
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound"):
            return error(409, "NOT_UPLOADED", "The file has not arrived in storage yet.")
        raise

    size = int(head.get("ContentLength") or 0)
    spec = limits.GROUPS[item["group"]]
    if size == 0:
        return _reject(trial_id, item, "EMPTY_FILE", "The uploaded file is empty.")
    if size != item["declaredSizeBytes"] or size > spec["maxBytes"]:
        return _reject(trial_id, item, "SIZE_MISMATCH",
                       f"The uploaded file is {size:,} bytes, not the {item['declaredSizeBytes']:,} "
                       "bytes that were registered.")
    stored_type = (head.get("ContentType") or "").lower()
    if stored_type != item["contentType"]:
        return _reject(trial_id, item, "SIGNATURE_MISMATCH",
                       "The file was stored with a different content type than registered.")

    first = s3().get_object(Bucket=bucket, Key=item["s3Key"], Range="bytes=0-63")["Body"].read()
    if not validate.signature_ok(item["contentType"], first):
        return _reject(trial_id, item, "SIGNATURE_MISMATCH",
                       f"The file's contents are not a valid {item['contentType']} file.")

    if item["group"] in ("trace", "bill"):
        data = process.read_object(item["s3Key"])
        try:
            result = process.process_trace(data) if item["group"] == "trace" else process.process_bill()
        except validate.TraceError as exc:
            return _reject(trial_id, item, "INVALID_TRACE", f"The GPS trace is not valid: {exc}.")
        updated = repo.update_evidence(
            trial_id, item["evidenceId"],
            {"state": "READY", "sizeBytes": size, "sha256": process.sha256(data),
             "result": result, "updatedAt": repo.now_iso(), "processedAt": repo.now_iso()},
            expect_states=("UPLOADING", "UPLOADED"),
        )
        return response(200, {"evidence": evidence_view(trial_id, updated)})

    try:
        repo.update_evidence(
            trial_id, item["evidenceId"],
            {"state": "UPLOADED", "sizeBytes": size, "updatedAt": repo.now_iso()},
            expect_states=("UPLOADING", "UPLOADED"),
        )
    except repo.StateConflict:
        # A concurrent /complete already queued it.
        return response(200, {"evidence": evidence_view(
            trial_id, repo.get_evidence(trial_id, item["evidenceId"]))})
    return _queue(trial_id, item["evidenceId"], from_states=("UPLOADED",), count_attempt=True)


def _queue(trial_id, evidence_id, *, from_states, count_attempt):
    current = repo.get_evidence(trial_id, evidence_id)
    fields = {"state": "QUEUED", "updatedAt": repo.now_iso()}
    if count_attempt:
        fields["attempts"] = int(current.get("attempts") or 0) + 1
    try:
        repo.update_evidence(trial_id, evidence_id, fields, expect_states=from_states)
    except repo.StateConflict:
        return error(409, "BAD_STATE", "The evidence changed state; reload the trial.")

    try:
        mode = process.dispatch(trial_id, evidence_id)
    except process.ProcessorUnavailable:
        repo.update_evidence(trial_id, evidence_id, {"state": "UPLOADED"},
                             expect_states=("QUEUED",))
        return error(503, "PROCESSOR_UNAVAILABLE",
                     "Evidence processing is not configured on this deployment.")
    except Exception as exc:
        log("trial_dispatch_failed", trialId=trial_id, evidenceId=evidence_id,
            error=type(exc).__name__)
        try:
            repo.update_evidence(trial_id, evidence_id, {"state": "UPLOADED"},
                                 expect_states=("QUEUED",))
        except repo.StateConflict:
            pass
        return error(503, "PROCESSOR_UNAVAILABLE", "Processing could not be started; try again.")

    log("trial_evidence_queued", trialId=trial_id, evidenceId=evidence_id, mode=mode)
    return response(202, {"evidence": evidence_view(trial_id, repo.get_evidence(trial_id, evidence_id)),
                          "processing": mode})


def retry(event, params, body):
    trial = authorise(event, params.get("trialId"))
    item = evidence_or_404(trial["trialId"], params.get("evidenceId"))
    if item["group"] not in ("photo", "slip"):
        return error(409, "BAD_STATE", "Only photos and slips are processed, so only they retry.")
    if (item.get("attempts") or 0) >= limits.MAX_RETRIES + 1:
        return error(409, "RETRY_LIMIT", "This file has been retried as often as allowed.")
    if not retryable(item):
        code = (item.get("error") or {}).get("code")
        if code in NOT_RETRYABLE:
            return error(409, "NOT_RETRYABLE", "This failure will not change on a retry.",
                         reason=code)
        return error(409, "BAD_STATE", f"A {item['state']} file has nothing to retry.")
    return _queue(trial["trialId"], item["evidenceId"], from_states=(item["state"],),
                  count_attempt=True)


def delete_evidence(event, params, body):
    trial = authorise(event, params.get("trialId"))
    trial_id = trial["trialId"]
    item = evidence_or_404(trial_id, params.get("evidenceId"))
    if item["state"] in ("QUEUED", "PROCESSING") and not process.stalled(item):
        return error(409, "BAD_STATE", "Wait for processing to finish before removing this file.")
    bucket = bucket_or_refuse()
    keys = [item["s3Key"], process.processing_key(trial_id, item["evidenceId"])]
    for key in keys:
        s3().delete_object(Bucket=bucket, Key=key)
    repo.delete_evidence_item(trial_id, item["evidenceId"])
    # A REJECTED item already gave its slot back, if its link had expired; if
    # not, the slot stays taken, like any other file whose link is still live.
    released = item["state"] != "REJECTED" and release_if_closed(trial_id, item)
    log("trial_evidence_deleted", trialId=trial_id, evidenceId=item["evidenceId"],
        released=released)
    return response(200, {"deleted": item["evidenceId"], "slotReleased": released})


PENDING = ("UPLOADED", "QUEUED", "PROCESSING")


def reference_note(details):
    """What the provenance says about the locations: only what was supplied."""
    supplied = [name for key, name in (("drainLocation", "Drain location"),
                                       ("disposalSite", "Disposal site")) if details.get(key)]
    if not supplied:
        return "No drain location or disposal site was supplied, so checks against them did not run."
    return (" and ".join(supplied) + (" were" if len(supplied) > 1 else " was")
            + " supplied for this trial; not an official record.")


def analyze(event, params, body):
    trial = authorise(event, params.get("trialId"))
    trial_id = trial["trialId"]
    evidence = repo.list_evidence(trial_id)

    pending = [item["evidenceId"] for item in evidence
               if item["state"] in PENDING and not process.stalled(item)]
    if pending:
        return error(409, "EVIDENCE_PROCESSING",
                     "Some files are still being processed. Try again in a few seconds.",
                     pending=pending)

    try:
        count = repo.reserve_trial_counter(trial_id, "analysisCount", limits.max_analyses())
    except repo.LimitReached:
        return error(429, "ANALYSIS_LIMIT",
                     f"A trial can be analysed at most {limits.max_analyses()} times.")

    ready, excluded = [], []
    trace_points = {}
    for item in evidence:
        if item["state"] != "READY":
            reason = (item.get("error") or {}).get("message") or (
                "Upload was never completed." if item["state"] == "UPLOADING"
                else "Processing did not finish; retry or remove this file."
                if process.stalled(item) else item["state"])
            excluded.append({"evidenceId": item["evidenceId"], "filename": item.get("filename"),
                             "state": item["state"], "reason": reason})
            continue
        if item["group"] == "trace":
            try:
                trace_points[item["evidenceId"]] = validate.parse_trace(
                    process.read_object(item["s3Key"]))["points"]
            except Exception as exc:
                excluded.append({"evidenceId": item["evidenceId"],
                                 "filename": item.get("filename"), "state": "READY",
                                 "reason": f"The trace could not be read back ({type(exc).__name__})."})
                continue
        ready.append(item)

    checks, counts = analysis.evaluate(trial, ready, trace_points)
    observed = analysis.observations(ready)
    mocked = config.mock_aws() or any(
        (obs.get("vision") or {}).get("mocked") or obs.get("mocked") for obs in observed)

    drain = (trial.get("details") or {}).get("drainLocation") or {}
    for item in ready:
        if item["group"] == "trace" and validate.swapped_hint(
                trace_points.get(item["evidenceId"]), drain.get("point")):
            for obs in observed:
                if obs["evidenceId"] == item["evidenceId"]:
                    obs.setdefault("warnings", []).append(
                        "This trace is far from the drain but would be near it with latitude "
                        "and longitude swapped; check the coordinate order.")

    result = {
        "trialId": trial_id,
        "analysisId": ids.new_analysis_id(),
        "analyzedAt": repo.now_iso(),
        "analysisCount": count,
        "evidenceConsidered": [item["evidenceId"] for item in ready],
        "evidenceExcluded": excluded,
        "checks": checks,
        "counts": counts,
        "observations": observed,
        "summary": {"text": analysis.summary_text(checks, counts, mocked=mocked),
                    "generatedBy": "template"},
        "provenance": {
            "mockAws": config.mock_aws(),
            "mocked": mocked,
            "visionModelId": None if config.mock_aws() else config.vision_model_id(),
            "textract": "AnalyzeDocument QUERIES",
            "rulesVersion": analysis.RULES_VERSION,
            "notes": [
                "Rules ran in the API Lambda; /analyze calls no model.",
                "Photo and slip readings were made once, when each file was processed.",
                reference_note(trial.get("details") or {}),
            ],
        },
    }
    repo.put_result(trial_id, result, trial["ttl"])
    repo.update_trial(trial_id, {"status": "ANALYZED", "lastAnalyzedAt": result["analyzedAt"]})
    log("trial_analyzed", trialId=trial_id, **{k.lower(): v for k, v in counts.items()})
    return response(200, result)


def get_results(event, params, body):
    trial = authorise(event, params.get("trialId"))
    result = repo.get_result(trial["trialId"])
    if not result:
        return error(404, "NO_ANALYSIS", "This trial has not been analysed yet.")
    return response(200, result)


def delete_trial(event, params, body):
    trial = authorise(event, params.get("trialId"))
    trial_id = trial["trialId"]
    bucket = bucket_or_refuse()
    prefix = process.trial_prefix(trial_id)
    client = s3()
    deleted_objects = 0
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        listing = client.list_objects_v2(**kwargs)
        keys = [{"Key": obj["Key"]} for obj in listing.get("Contents", [])
                if obj["Key"].startswith(prefix)]
        if keys:
            client.delete_objects(Bucket=bucket, Delete={"Objects": keys, "Quiet": True})
            deleted_objects += len(keys)
        if not listing.get("IsTruncated"):
            break
        token = listing.get("NextContinuationToken")
    deleted_items = repo.delete_trial_items(trial_id)
    log("trial_deleted", trialId=trial_id, objects=deleted_objects, items=deleted_items)
    return response(200, {"deleted": trial_id, "objects": deleted_objects, "records": deleted_items})


ROUTES = {
    "POST /trials": create_trial,
    "GET /trials/{trialId}": get_trial,
    "PUT /trials/{trialId}/details": put_details,
    "POST /trials/{trialId}/upload-url": upload_url,
    "POST /trials/{trialId}/evidence/{evidenceId}/complete": complete,
    "POST /trials/{trialId}/evidence/{evidenceId}/retry": retry,
    "DELETE /trials/{trialId}/evidence/{evidenceId}": delete_evidence,
    "POST /trials/{trialId}/analyze": analyze,
    "GET /trials/{trialId}/results": get_results,
    "DELETE /trials/{trialId}": delete_trial,
}


def handle(event):
    route_key = event.get("routeKey", "")
    handler = ROUTES.get(route_key)
    if handler is None:
        return error(404, "NO_ROUTE", f"No route for {route_key or 'request'}")

    raw = event.get("body")
    if raw and event.get("isBase64Encoded"):
        import base64

        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except Exception:
            return error(400, "INVALID_JSON", "Body is not valid JSON")
    try:
        body = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        return error(400, "INVALID_JSON", "Body is not valid JSON")
    if not isinstance(body, dict):
        return error(400, "INVALID_JSON", "Body must be a JSON object")

    try:
        return handler(event, event.get("pathParameters") or {}, body)
    except Refused as refused:
        return refused.reply
    except Exception as exc:
        log("trial_request_failed", routeKey=route_key, error=f"{type(exc).__name__}: {exc}"[:300])
        return error(500, "INTERNAL", "The request failed.", detail=type(exc).__name__)
