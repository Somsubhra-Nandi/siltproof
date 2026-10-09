"""Processing one trial evidence file.

Photos and slips run in the ingest Lambda, which has Pillow and the AI
permissions; the API invokes it asynchronously with
``{"trialProcess": {"trialId": ..., "evidenceId": ...}}``. Traces and bill
documents need no AI and are handled inside the API's ``…/complete`` call.

Order for a photo, which the Kolkata requirements fix:

    original bytes -> SHA-256 -> EXIF (GPS, time, camera) -> pHash
                   -> [processing copy, only if Bedrock needs one]
                   -> reserve quota -> Bedrock -> store verbatim result

The original object is never rewritten. Billable calls are reserved before
they are made and skipped when the stored result already matches the bytes.
"""

import datetime
import hashlib
import io
import json

from common import awsclients, bedrock, config, textract
# common.photo needs Pillow and imagehash, which only the ingest function has
# (its layer). The API Lambda imports this module too, so photo is imported
# inside the functions that run in ingest.
from common.jsonlog import log

from . import limits, repo, validate

S3_PREFIX = "trials"


class ProcessorUnavailable(Exception):
    pass


class ProcessingFailed(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def original_key(trial_id, evidence_id, extension):
    return f"{S3_PREFIX}/{trial_id}/originals/{evidence_id}{extension}"


def processing_key(trial_id, evidence_id):
    return f"{S3_PREFIX}/{trial_id}/processing/{evidence_id}.jpg"


def trial_prefix(trial_id):
    repo.trial_pk(trial_id)  # validates the ID
    return f"{S3_PREFIX}/{trial_id}/"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_object(key):
    response = awsclients.client("s3").get_object(Bucket=config.evidence_bucket(), Key=key)
    return response["Body"].read()


# ----------------------------------------------------------------- dispatch
def dispatch(trial_id, evidence_id):
    """Start processing. Async Lambda invoke when deployed; inline offline."""
    function = limits.processor_function()
    if function:
        awsclients.client("lambda").invoke(
            FunctionName=function,
            InvocationType="Event",
            Payload=json.dumps(
                {"trialProcess": {"trialId": trial_id, "evidenceId": evidence_id}}
            ).encode("utf-8"),
        )
        return "invoked"
    if config.mock_aws():
        run(trial_id, evidence_id)
        return "inline"
    raise ProcessorUnavailable("TRIAL_PROCESSOR_FUNCTION is not configured")


def handle_event(event):
    """Entry point from the ingest Lambda. Never raises, so Lambda's own async
    retries cannot repeat a billable call; failures are stored on the item."""
    request = event.get("trialProcess") or {}
    trial_id, evidence_id = request.get("trialId"), request.get("evidenceId")
    from . import ids

    if not ids.valid_trial_id(trial_id) or not ids.valid_evidence_id(evidence_id):
        log("trial_process_ignored", reason="bad_ids")
        return {"status": "ignored"}
    try:
        return {"status": run(trial_id, evidence_id)}
    except Exception as exc:  # stored on the item by run(); this is the last guard
        log("trial_process_crashed", trialId=trial_id, evidenceId=evidence_id,
            error=type(exc).__name__)
        return {"status": "error"}


# ---------------------------------------------------------------------- run
def run(trial_id, evidence_id):
    item = repo.claim_for_processing(trial_id, evidence_id)
    if item is None:
        log("trial_process_skipped", trialId=trial_id, evidenceId=evidence_id,
            reason="not_queued_or_leased")
        return "skipped"

    try:
        data = read_object(item["s3Key"])
        digest = sha256(data)
        check_unchanged(item, data)
        if item["group"] == "photo":
            result, notes = process_photo(trial_id, item, data, digest)
        elif item["group"] == "slip":
            result, notes = process_slip(trial_id, item, data, digest)
        else:
            raise ProcessingFailed("BAD_STATE", f"{item['group']} is not processed here")
    except ProcessingFailed as exc:
        _fail(trial_id, evidence_id, exc.code, exc.message)
        return "failed"
    except Exception as exc:
        log("trial_process_error", trialId=trial_id, evidenceId=evidence_id,
            error=f"{type(exc).__name__}: {exc}"[:300])
        _fail(trial_id, evidence_id, "PROCESSING_ERROR",
              f"Processing stopped with {type(exc).__name__}.")
        return "failed"

    repo.update_evidence(
        trial_id, evidence_id,
        {"state": "READY", "result": result, "sha256": digest, "sizeBytes": len(data),
         "error": notes, "updatedAt": repo.now_iso(), "processedAt": repo.now_iso()},
        expect_states=("PROCESSING",),
        remove=("leaseUntil",),
    )
    log("trial_evidence_ready", trialId=trial_id, evidenceId=evidence_id, group=item["group"])
    return "processed"


def check_unchanged(item, data):
    """The presigned POST stays valid for its whole lifetime, so the object can
    be replaced after /complete validated it. Read it again on the same terms
    before any billable call."""
    # /complete only accepts a file of exactly the declared size.
    if len(data) != item.get("declaredSizeBytes") or not validate.signature_ok(
            item.get("contentType"), data[:64]):
        raise ProcessingFailed(
            "CHANGED_AFTER_UPLOAD",
            "The stored file no longer matches the one that was checked at upload.",
        )


def stalled(item, now=None):
    """True when a QUEUED or PROCESSING item will never finish on its own: the
    processor crashed past its lease, or the async invocation was lost."""
    now = now if now is not None else repo.now_epoch()
    if item.get("state") == "PROCESSING":
        return int(item.get("leaseUntil") or 0) < now
    if item.get("state") == "QUEUED":
        queued = item.get("updatedAt") or item.get("createdAt")
        try:
            at = datetime.datetime.fromisoformat(queued).timestamp()
        except (TypeError, ValueError):
            return True
        return at + limits.QUEUE_STALL_S < now
    return False


def _fail(trial_id, evidence_id, code, message, result=None):
    fields = {"state": "FAILED", "error": {"code": code, "message": message},
              "updatedAt": repo.now_iso()}
    if result is not None:
        fields["result"] = result
    try:
        repo.update_evidence(trial_id, evidence_id, fields,
                             expect_states=("PROCESSING",), remove=("leaseUntil",))
    except repo.StateConflict:
        log("trial_fail_lost_lease", trialId=trial_id, evidenceId=evidence_id)


# ------------------------------------------------------------------- quotas
def reserve_model_call(trial_id, kind):
    """Per-trial then global daily allowance. Raises repo.LimitReached."""
    if kind == "bedrock":
        repo.reserve_trial_counter(trial_id, "bedrockCalls", limits.max_bedrock_per_trial())
        repo.reserve_daily("BEDROCK", limits.daily_bedrock_calls())
    else:
        repo.reserve_trial_counter(trial_id, "textractCalls", limits.max_textract_per_trial())
        repo.reserve_daily("TEXTRACT", limits.daily_textract_calls())


# -------------------------------------------------------------------- photo
EXIF_ORIENTATION = 274
GPS_H_POSITIONING_ERROR = 31
TAG_OFFSET_ORIGINAL = 36881


def exif_extras(data):
    """What the B1 reader does not keep: orientation, offset, GPS accuracy."""
    from PIL import Image

    from common import photo

    extras = {"orientation": None, "timestampHasOffset": False, "gpsAccuracyM": None}
    try:
        with Image.open(io.BytesIO(data)) as image:
            exif = image.getexif()
            extras["orientation"] = exif.get(EXIF_ORIENTATION)
            try:
                extras["timestampHasOffset"] = bool(
                    (exif.get_ifd(photo.EXIF_IFD) or {}).get(TAG_OFFSET_ORIGINAL)
                )
            except Exception:
                pass
            try:
                accuracy = (exif.get_ifd(photo.GPS_IFD) or {}).get(GPS_H_POSITIONING_ERROR)
                extras["gpsAccuracyM"] = photo._rational(accuracy) if accuracy is not None else None
            except Exception:
                pass
    except Exception:
        pass
    return extras


def clean_camera(text):
    """Phones pad the EXIF make/model with NULs (KOLKATA-FIELD-EVIDENCE.md)."""
    if not text:
        return None
    cleaned = " ".join(str(text).replace("\x00", " ").split())
    return cleaned[:80] or None


def make_processing_copy(trial_id, evidence_id, data, original_key_, original_digest):
    """A downscaled JPEG for Bedrock, stored beside (never over) the original."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as image:
        original_pixels = list(image.size)
        upright = ImageOps.exif_transpose(image).convert("RGB")
        upright.thumbnail((limits.PROCESSING_COPY_SIDE, limits.PROCESSING_COPY_SIDE))
        out = io.BytesIO()
        upright.save(out, format="JPEG", quality=85)
        copy_pixels = list(upright.size)

    copy = out.getvalue()
    key = processing_key(trial_id, evidence_id)
    awsclients.client("s3").put_object(
        Bucket=config.evidence_bucket(), Key=key, Body=copy, ContentType="image/jpeg",
        Metadata={"original-key": original_key_, "original-sha256": original_digest},
    )
    return copy, {
        "key": key,
        "originalKey": original_key_,
        "originalSha256": original_digest,
        "copySha256": sha256(copy),
        "originalPixels": original_pixels,
        "copyPixels": copy_pixels,
        "reason": "Bedrock input limit",
    }


def process_photo(trial_id, item, data, digest):
    from common import photo

    exif = photo.read_exif(data)
    if any(problem.startswith("unreadable_image") for problem in exif["problems"]):
        raise ProcessingFailed("UNREADABLE_IMAGE", "The file could not be read as an image.")
    # The header gives the size without decoding; refuse before pHash or a
    # processing copy decodes a pixel bomb.
    if (exif.get("width") or 0) * (exif.get("height") or 0) > limits.MAX_PHOTO_PIXELS:
        raise ProcessingFailed(
            "IMAGE_TOO_LARGE",
            f"The image is over {limits.MAX_PHOTO_PIXELS // 1_000_000} megapixels.",
        )

    extras = exif_extras(data)
    phash = photo.perceptual_hash(data)

    exif_out = {
        "timestamp": exif["timestamp"],
        "timestampHasOffset": extras["timestampHasOffset"],
        "lat": exif["lat"], "lon": exif["lon"], "hasGps": exif["hasGps"],
        "hasTimestamp": exif["hasTimestamp"],
        "altitudeM": exif.get("altitudeM"),
        "gpsAccuracyM": extras["gpsAccuracyM"],
        "camera": clean_camera(exif.get("camera")),
        "width": exif.get("width"), "height": exif.get("height"),
        "orientation": extras["orientation"],
        "problems": list(exif["problems"]) + ([] if phash else ["phash_failed"]),
    }
    if item.get("contentType") == "image/png" and not exif["hasGps"]:
        exif_out["problems"].append("png_without_gps")

    result = {"exif": exif_out, "pHash": phash, "processingCopy": None}

    previous = (item.get("result") or {}).get("vision") or {}
    if previous.get("ran") and item.get("sha256") == digest:
        # The same bytes were already read by the model: never pay twice.
        result["vision"] = previous
        result["processingCopy"] = (item.get("result") or {}).get("processingCopy")
        return result, None

    model_input, input_kind = data, "original"
    needs_copy = len(data) > limits.MAX_BYTES_FOR_BEDROCK or max(
        exif.get("width") or 0, exif.get("height") or 0
    ) > limits.MAX_PIXELS_SIDE_FOR_BEDROCK
    if needs_copy:
        model_input, result["processingCopy"] = make_processing_copy(
            trial_id, item["evidenceId"], data, item["s3Key"], digest
        )
        input_kind = "processing_copy"

    try:
        reserve_model_call(trial_id, "bedrock")
    except repo.LimitReached as exc:
        result["vision"] = _vision_skipped("QUOTA_EXHAUSTED", input_kind,
                                           f"Model allowance reached ({exc.what}); no call was made.")
        return result, {"code": "QUOTA_EXHAUSTED",
                        "message": "The photo was read, but the model allowance is used up."}

    content_type = "image/jpeg" if input_kind == "processing_copy" else item.get("contentType")
    try:
        response = bedrock.check_photo(model_input, key=item["s3Key"], content_type=content_type)
    except Exception as exc:
        log("trial_bedrock_failed", trialId=trial_id, evidenceId=item["evidenceId"],
            error=type(exc).__name__)
        result["vision"] = _vision_skipped("BEDROCK_FAILED", input_kind,
                                           f"The model call failed ({type(exc).__name__}).")
        return result, {"code": "BEDROCK_FAILED",
                        "message": "The photo was read, but the model call failed. It can be retried."}

    verdict = bedrock.parse_photo_check(response)
    mocked = config.mock_aws()
    result["vision"] = {
        "cleared": verdict["cleared"],
        "loadType": verdict["load_type"],
        "confidence": verdict["confidence"],
        "notes": verdict["notes"],
        "ok": verdict["ok"],
        "problems": verdict.get("problems") or [],
        "source": verdict.get("source"),
        "stopReason": verdict.get("stopReason"),
        "modelId": None if mocked else config.vision_model_id(),
        "mocked": mocked,
        "ran": True,
        "skippedReason": None,
        "input": input_kind,
        "prompt": "common.bedrock.PHOTO_PROMPT",
    }
    log("trial_bedrock_photo_checked", trialId=trial_id, evidenceId=item["evidenceId"],
        mocked=mocked, input=input_kind, usage=response.get("usage"))
    return result, None


def _vision_skipped(reason, input_kind, notes):
    return {
        "cleared": None, "loadType": None, "confidence": None, "notes": notes,
        "ok": False, "problems": [], "modelId": None, "mocked": config.mock_aws(),
        "ran": False, "skippedReason": reason, "input": input_kind,
    }


# --------------------------------------------------------------------- slip
def process_slip(trial_id, item, data, digest):
    previous = item.get("result") or {}
    if previous.get("fields") and item.get("sha256") == digest:
        return previous, None

    if len(data) > limits.MAX_BYTES_FOR_TEXTRACT:
        raise ProcessingFailed("FILE_TOO_LARGE", "Textract reads slips of 10 MB or less.")

    if item.get("contentType") == "application/pdf":
        pages = validate.pdf_page_count(data)
        if pages > 1:
            raise ProcessingFailed(
                "PDF_TOO_MANY_PAGES",
                f"The PDF has {pages} pages; upload each slip as a single-page PDF or an image.",
            )

    try:
        reserve_model_call(trial_id, "textract")
    except repo.LimitReached as exc:
        raise ProcessingFailed(
            "QUOTA_EXHAUSTED", f"The Textract allowance is used up ({exc.what}); no call was made."
        ) from exc

    try:
        response = textract.analyze_document(data, key=item["s3Key"])
    except Exception as exc:
        log("trial_textract_failed", trialId=trial_id, evidenceId=item["evidenceId"],
            error=type(exc).__name__)
        raise ProcessingFailed(
            "TEXTRACT_FAILED", f"Textract could not read the slip ({type(exc).__name__})."
        ) from exc

    parsed = textract.parse_slip(response)
    mocked = config.mock_aws()
    return {
        "fields": parsed["fields"],
        "confidenceAvg": parsed["confidenceAvg"],
        "missingFields": parsed["missingFields"],
        "lowConfidenceFields": parsed["lowConfidenceFields"],
        "modelVersion": parsed["modelVersion"],
        "service": "Amazon Textract AnalyzeDocument (QUERIES)",
        "mocked": mocked,
    }, None


# -------------------------------------------- inline groups (API Lambda)
def process_trace(data):
    """Validate and summarise a trace. Raises validate.TraceError."""
    from common import billdata

    trace = validate.parse_trace(data)
    points = trace["points"]
    route = [[point["lon"], point["lat"]] for point in billdata.downsample(points, 300)]
    lons = [point["lon"] for point in points]
    lats = [point["lat"] for point in points]
    from common import rules

    gap = rules.max_gap_seconds(points)
    return {
        "format": trace["format"],
        "vehicleNo": trace["vehicleNo"],
        "pointCount": trace["pointCount"],
        "startTime": trace["startTime"],
        "endTime": trace["endTime"],
        "distanceM": trace["distanceM"],
        "maxSpeedKmh": trace["maxSpeedKmh"],
        "maxGapSeconds": round(gap, 1) if gap is not None else None,
        "bbox": [min(lons), min(lats), max(lons), max(lats)],
        "route": route,
        "warnings": trace["warnings"],
    }


def process_bill():
    return {
        "stored": True,
        "extraction": "not_supported",
        "note": "SiltProof does not extract bills; the claim comes from the fields entered.",
    }
