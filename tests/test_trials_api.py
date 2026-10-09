"""Judge-trial API, offline (moto S3 + DynamoDB, MOCK_AWS=1).

Covers docs/JUDGE-TRIAL-API.md: creation, access control, upload scope and
validation, processing, quotas, retries, analysis, cleanup and isolation from
Bill B1. Nothing here reaches AWS.
"""

import base64
import hashlib
import json

import pytest

from common import store
from trial import ids, limits, process, repo
from trial_support import (DUMP, KOLKATA, call, create_trial, drive, jpeg, minimal_pdf, png,
                           trace_json, upload)

COMPLETE = "POST /trials/{trialId}/evidence/{evidenceId}/complete"
RETRY = "POST /trials/{trialId}/evidence/{evidenceId}/retry"


def get(trial_id, token):
    status, payload = call("GET /trials/{trialId}", token=token, trialId=trial_id)
    assert status == 200, payload
    return payload


def evidence_of(trial_id, token, evidence_id):
    return next(item for item in get(trial_id, token)["evidence"]
                if item["evidenceId"] == evidence_id)


def details(trial_id, token, **body):
    status, payload = call("PUT /trials/{trialId}/details", token=token, trialId=trial_id,
                           body=body)
    assert status == 200, payload
    return payload


def analyze(trial_id, token):
    return call("POST /trials/{trialId}/analyze", token=token, trialId=trial_id)


def checks_by(result, rule):
    return [check for check in result["checks"] if check["id"] == rule]


# ---------------------------------------------------------------- creation
def test_create_returns_unpredictable_ids_and_stores_only_the_token_hash(aws):
    seen_ids, seen_tokens = set(), set()
    for _ in range(25):
        trial_id, token = create_trial()
        assert ids.valid_trial_id(trial_id)
        assert len(token) >= 43
        seen_ids.add(trial_id)
        seen_tokens.add(token)
        item = repo.get_trial(trial_id)
        assert item["tokenHash"] == hashlib.sha256(token.encode()).hexdigest()
        assert token not in json.dumps(item)
    assert len(seen_ids) == 25 and len(seen_tokens) == 25


def test_create_sets_retention_and_privacy_notice(aws):
    status, payload = call("POST /trials", body={"kind": "field", "label": "New Town"})
    assert status == 201
    item = repo.get_trial(payload["trialId"])
    assert item["ttl"] - repo.now_epoch() == pytest.approx(48 * 3600, abs=5)
    assert payload["privacy"]["retentionHours"] == 48
    assert "Amazon Bedrock" in " ".join(payload["privacy"]["services"])
    assert "GPS" in payload["privacy"]["notice"] or "EXIF" in payload["privacy"]["notice"]


def test_create_rejects_unknown_kind_and_control_characters_are_stripped(aws):
    assert call("POST /trials", body={"kind": "admin"})[0] == 400
    status, payload = call("POST /trials", body={"label": "bad\x00label"})
    assert status == 201 and payload["label"] == "bad label"


def test_invite_code_is_enforced_server_side(aws, monkeypatch):
    monkeypatch.setenv("TRIAL_INVITE_CODE", "river-2026")
    assert call("POST /trials", body={})[0] == 403
    assert call("POST /trials", body={"inviteCode": "wrong"})[0] == 403
    assert call("POST /trials", body={"inviteCode": "river-2026"})[0] == 201


def test_daily_trial_quota_fails_closed(aws, monkeypatch):
    monkeypatch.setenv("TRIAL_DAILY_TRIALS", "2")
    create_trial()
    create_trial()
    status, payload = call("POST /trials", body={})
    assert status == 429 and payload["code"] == "TRIAL_QUOTA_EXHAUSTED"


# ------------------------------------------------------------ access control
def test_access_needs_the_trials_own_token(aws):
    trial_a, token_a = create_trial()
    trial_b, token_b = create_trial()

    assert call("GET /trials/{trialId}", trialId=trial_a)[0] == 404
    assert call("GET /trials/{trialId}", token="nope", trialId=trial_a)[0] == 404
    status, payload = call("GET /trials/{trialId}", token=token_b, trialId=trial_a)
    assert status == 404 and payload["code"] == "TRIAL_NOT_FOUND"
    assert call("GET /trials/{trialId}", token=token_a, trialId="B1")[0] == 404
    assert call("GET /trials/{trialId}", token=token_a, trialId="../B1")[0] == 404
    assert call("GET /trials/{trialId}", token=token_a, trialId=trial_a)[0] == 200


def test_every_trial_route_refuses_another_trials_token(aws):
    trial_a, token_a = create_trial()
    _, token_b = create_trial()
    _, _, evidence_id = upload(aws, trial_a, token_a, "photo", jpeg(3), "image/jpeg")
    params = {"trialId": trial_a, "evidenceId": evidence_id}
    for route in ("GET /trials/{trialId}", "PUT /trials/{trialId}/details",
                  "POST /trials/{trialId}/upload-url", COMPLETE, RETRY,
                  "DELETE /trials/{trialId}/evidence/{evidenceId}",
                  "POST /trials/{trialId}/analyze", "GET /trials/{trialId}/results",
                  "DELETE /trials/{trialId}"):
        status, payload = call(route, token=token_b, body={}, **params)
        assert status == 404, (route, payload)
    # ...and trial A is still intact.
    assert len(get(trial_a, token_a)["evidence"]) == 1


def test_evidence_of_another_trial_is_not_reachable(aws):
    trial_a, token_a = create_trial()
    trial_b, token_b = create_trial()
    _, _, evidence_a = upload(aws, trial_a, token_a, "photo", jpeg(4), "image/jpeg")
    status, payload = call(COMPLETE, token=token_b, trialId=trial_b, evidenceId=evidence_a)
    assert status == 404 and payload["code"] == "EVIDENCE_NOT_FOUND"
    status, _ = call("DELETE /trials/{trialId}/evidence/{evidenceId}", token=token_b,
                     trialId=trial_b, evidenceId=evidence_a)
    assert status == 404


def test_expired_trial_is_refused(aws):
    trial_id, token = create_trial()
    repo.update_trial(trial_id, {"ttl": repo.now_epoch() - 1})
    status, payload = call("GET /trials/{trialId}", token=token, trialId=trial_id)
    assert status == 410 and payload["code"] == "TRIAL_EXPIRED"


# ------------------------------------------------------------------ uploads
def test_upload_url_is_a_scoped_short_lived_post_policy(aws):
    trial_id, token = create_trial()
    status, payload = call("POST /trials/{trialId}/upload-url", token=token, trialId=trial_id,
                           body={"group": "photo", "role": "before", "filename": "../../x.jpg",
                                 "contentType": "image/jpeg", "sizeBytes": 7_700_000,
                                 "key": "photos/B1/drain14/after-1.jpg"})
    assert status == 201, payload
    upload_ = payload["upload"]
    key = upload_["fields"]["key"]
    assert key == f"trials/{trial_id}/originals/{payload['evidenceId']}.jpg"
    assert upload_["method"] == "POST" and upload_["expiresInSeconds"] == 300

    policy = json.loads(base64.b64decode(upload_["fields"]["policy"]))
    conditions = policy["conditions"]
    assert {"key": key} in conditions or {"bucket": aws["bucket"]} in conditions
    assert ["content-length-range", 1, 7_700_000] in conditions
    assert {"Content-Type": "image/jpeg"} in conditions
    assert payload["evidence"]["filename"] == ".._.._x.jpg"
    assert payload["evidence"]["state"] == "UPLOADING"


@pytest.mark.parametrize("body, code", [
    ({"group": "video", "contentType": "video/mp4", "sizeBytes": 10}, "UNSUPPORTED_GROUP"),
    ({"group": "photo", "contentType": "image/gif", "sizeBytes": 10}, "UNSUPPORTED_TYPE"),
    ({"group": "photo", "contentType": "image/heic", "sizeBytes": 10}, "UNSUPPORTED_TYPE"),
    ({"group": "slip", "contentType": "image/webp", "sizeBytes": 10}, "UNSUPPORTED_TYPE"),
    ({"group": "trace", "contentType": "text/csv", "sizeBytes": 10}, "UNSUPPORTED_TYPE"),
    ({"group": "trace", "contentType": "application/gpx+xml", "sizeBytes": 10}, "UNSUPPORTED_TYPE"),
    ({"group": "photo", "contentType": "image/jpeg", "sizeBytes": 0}, "EMPTY_FILE"),
    ({"group": "photo", "contentType": "image/jpeg", "sizeBytes": "5"}, "INVALID_REQUEST"),
    ({"group": "photo", "contentType": "image/jpeg", "sizeBytes": 16 * 1024 * 1024}, "FILE_TOO_LARGE"),
    ({"group": "slip", "contentType": "application/pdf", "sizeBytes": 11 * 1024 * 1024}, "FILE_TOO_LARGE"),
    ({"group": "photo", "role": "proof", "contentType": "image/jpeg", "sizeBytes": 10}, "INVALID_ROLE"),
    ({"group": "slip", "role": "after", "contentType": "image/jpeg", "sizeBytes": 10}, "INVALID_ROLE"),
])
def test_upload_url_rejects_unsupported_requests(aws, body, code):
    trial_id, token = create_trial()
    status, payload = call("POST /trials/{trialId}/upload-url", token=token, trialId=trial_id,
                           body=body)
    assert status in (400, 413) and payload["code"] == code


def test_a_10mb_plus_original_jpeg_is_accepted(aws):
    trial_id, token = create_trial()
    status, payload = call("POST /trials/{trialId}/upload-url", token=token, trialId=trial_id,
                           body={"group": "photo", "contentType": "image/jpeg",
                                 "sizeBytes": 15 * 1024 * 1024})
    assert status == 201


def test_per_group_file_limit(aws):
    trial_id, token = create_trial()
    for _ in range(limits.GROUPS["bill"]["maxFiles"]):
        assert call("POST /trials/{trialId}/upload-url", token=token, trialId=trial_id,
                    body={"group": "bill", "contentType": "application/pdf",
                          "sizeBytes": 100})[0] == 201
    status, payload = call("POST /trials/{trialId}/upload-url", token=token, trialId=trial_id,
                           body={"group": "bill", "contentType": "application/pdf",
                                 "sizeBytes": 100})
    assert status == 413 and payload["code"] == "TRIAL_FILE_LIMIT"


def test_per_trial_byte_budget(aws):
    trial_id, token = create_trial()
    big = 15 * 1024 * 1024
    for _ in range(8):   # 120 MB
        assert call("POST /trials/{trialId}/upload-url", token=token, trialId=trial_id,
                    body={"group": "photo", "contentType": "image/jpeg",
                          "sizeBytes": big})[0] == 201
    status, payload = call("POST /trials/{trialId}/upload-url", token=token, trialId=trial_id,
                           body={"group": "photo", "contentType": "image/jpeg", "sizeBytes": 1})
    assert status == 413 and payload["code"] == "TRIAL_BYTES_LIMIT"


def test_file_reservation_is_one_conditional_write(aws):
    """Race safety comes from DynamoDB evaluating the condition on the item's
    current value inside one UpdateItem; there is no read-then-write to race.

    moto does not serialise concurrent writes the way DynamoDB does, so a
    multi-threaded test against it would prove nothing either way. Instead this
    checks the shape of the request, and the stale-read case below checks the
    behaviour that shape gives.
    """
    from common import awsclients

    trial_id, _ = create_trial()
    sent = []

    def record(params, **kwargs):
        sent.append(params)

    client = awsclients.resource("dynamodb").meta.client
    client.meta.events.register("provide-client-params.dynamodb.*", record)
    try:
        repo.reserve_file(trial_id, "trace", 10)
        with pytest.raises(repo.LimitReached):
            repo.reserve_daily("TEXTRACT", 0)
        repo.reserve_daily("TEXTRACT", 3)
    finally:
        client.meta.events.unregister("provide-client-params.dynamodb.*", record)
    assert len(sent) == 2
    assert all("ConditionExpression" in params and "UpdateExpression" in params
               for params in sent)


def test_a_stale_reader_cannot_take_the_last_unit(aws):
    """Two requests both see one unit left; only the first update succeeds."""
    trial_id, token = create_trial()
    group_max = limits.GROUPS["trace"]["maxFiles"]
    for _ in range(group_max - 1):
        repo.reserve_file(trial_id, "trace", 10)
    seen_by_both = repo.get_trial(trial_id)["groupFiles"]["trace"]
    assert seen_by_both == group_max - 1
    repo.reserve_file(trial_id, "trace", 10)          # first racer
    with pytest.raises(repo.LimitReached):
        repo.reserve_file(trial_id, "trace", 10)      # second racer, same stale view
    for _ in range(4):
        repo.reserve_daily("BEDROCK", 5)
    assert repo.daily_count("BEDROCK") == 4
    repo.reserve_daily("BEDROCK", 5)
    with pytest.raises(repo.LimitReached):
        repo.reserve_daily("BEDROCK", 5)
    assert repo.daily_count("BEDROCK") == 5


def test_complete_before_upload_is_a_409(aws):
    trial_id, token = create_trial()
    _, _, evidence_id = upload(aws, trial_id, token, "photo", jpeg(5), "image/jpeg",
                               complete=False)
    aws["s3"].delete_object(Bucket=aws["bucket"],
                            Key=repo.get_evidence(trial_id, evidence_id)["s3Key"])
    status, payload = call(COMPLETE, token=token, trialId=trial_id, evidenceId=evidence_id)
    assert status == 409 and payload["code"] == "NOT_UPLOADED"


def test_wrong_signature_is_rejected_and_deleted_but_keeps_its_slot_while_the_link_lives(aws):
    trial_id, token = create_trial()
    fake = b"MZ\x90\x00 definitely not a pdf" * 10
    status, payload, evidence_id = upload(aws, trial_id, token, "slip", fake, "application/pdf")
    assert status == 422 and payload["code"] == "SIGNATURE_MISMATCH"
    assert payload["evidence"]["state"] == "REJECTED"
    key = repo.get_evidence(trial_id, evidence_id)["s3Key"]
    assert "Contents" not in aws["s3"].list_objects_v2(Bucket=aws["bucket"], Prefix=key)
    # The presigned POST could still write to the key, so the slot is not given back.
    trial = repo.get_trial(trial_id)
    assert trial["fileCount"] == 1 and trial["bytesReserved"] == len(fake)


def test_size_mismatch_is_rejected(aws):
    trial_id, token = create_trial()
    data = jpeg(6)
    status, payload, _ = upload(aws, trial_id, token, "photo", data, "image/jpeg",
                                put_data=data + b"\x00" * 100)
    assert status == 422 and payload["code"] == "SIZE_MISMATCH"


def test_retyped_upload_is_rejected(aws):
    trial_id, token = create_trial()
    status, payload, _ = upload(aws, trial_id, token, "photo", jpeg(7), "image/jpeg",
                                put_type="image/png")
    assert status == 422 and payload["code"] == "SIGNATURE_MISMATCH"


def test_png_bytes_declared_as_jpeg_are_rejected(aws):
    trial_id, token = create_trial()
    status, payload, _ = upload(aws, trial_id, token, "photo", png(1), "image/jpeg")
    assert status == 422 and payload["code"] == "SIGNATURE_MISMATCH"


def test_complete_twice_is_harmless(aws):
    trial_id, token = create_trial()
    _, _, evidence_id = upload(aws, trial_id, token, "photo", jpeg(8), "image/jpeg")
    status, payload = call(COMPLETE, token=token, trialId=trial_id, evidenceId=evidence_id)
    assert status == 200 and payload["evidence"]["state"] == "READY"
    assert repo.get_trial(trial_id)["bedrockCalls"] == 1


# ------------------------------------------------------------- processing
def test_photo_exif_hash_and_vision_come_from_the_untouched_original(aws):
    trial_id, token = create_trial()
    data = jpeg(9, lat=22.5801, lon=88.4712, model=b"Pixel 7\x00\x00\x00\x00")
    status, payload, evidence_id = upload(aws, trial_id, token, "photo", data, "image/jpeg",
                                          role="current", filename="IMG_1.jpg")
    assert status == 202, payload
    item = evidence_of(trial_id, token, evidence_id)
    assert item["state"] == "READY"
    exif = item["result"]["exif"]
    assert exif["hasGps"] and exif["lat"] == pytest.approx(22.5801, abs=1e-5)
    assert exif["lon"] == pytest.approx(88.4712, abs=1e-5)
    assert exif["timestamp"] == "2026-10-09T09:53:12"
    assert exif["timestampHasOffset"] is False
    assert exif["camera"] == "TestMake Pixel 7"           # NUL padding stripped
    assert len(item["result"]["pHash"]) == 16
    assert item["sha256"] == hashlib.sha256(data).hexdigest()

    stored = aws["s3"].get_object(Bucket=aws["bucket"],
                                  Key=repo.get_evidence(trial_id, evidence_id)["s3Key"])
    assert stored["Body"].read() == data                   # original preserved byte for byte

    vision = item["result"]["vision"]
    assert vision["mocked"] is True and vision["modelId"] is None
    assert vision["input"] == "original" and item["result"]["processingCopy"] is None
    assert "OFFLINE MOCK" in vision["notes"]
    assert item["previewUrl"] and evidence_id in item["previewUrl"]


def test_large_photo_gets_a_traceable_processing_copy(aws, monkeypatch):
    monkeypatch.setattr(limits, "MAX_BYTES_FOR_BEDROCK", 1000)
    sent = []
    from common import bedrock

    original_check = bedrock.check_photo

    def spy(image_bytes, **kwargs):
        sent.append((image_bytes, kwargs))
        return original_check(image_bytes, **kwargs)

    monkeypatch.setattr(bedrock, "check_photo", spy)
    trial_id, token = create_trial()
    data = jpeg(10, lat=22.58, lon=88.47, size=(2400, 1200), noise=True)
    _, _, evidence_id = upload(aws, trial_id, token, "photo", data, "image/jpeg")
    item = evidence_of(trial_id, token, evidence_id)
    copy = item["result"]["processingCopy"]
    assert copy["key"] == f"trials/{trial_id}/processing/{evidence_id}.jpg"
    assert copy["originalSha256"] == hashlib.sha256(data).hexdigest()
    assert copy["originalPixels"] == [2400, 1200]
    assert max(copy["copyPixels"]) == limits.PROCESSING_COPY_SIDE
    stored_copy = aws["s3"].get_object(Bucket=aws["bucket"], Key=copy["key"])["Body"].read()
    assert hashlib.sha256(stored_copy).hexdigest() == copy["copySha256"]
    assert sent and sent[0][0] == stored_copy and sent[0][1]["content_type"] == "image/jpeg"
    assert item["result"]["vision"]["input"] == "processing_copy"
    # EXIF still from the original, and the original is unchanged.
    assert item["result"]["exif"]["hasGps"]
    original = aws["s3"].get_object(Bucket=aws["bucket"],
                                    Key=repo.get_evidence(trial_id, evidence_id)["s3Key"])
    assert original["Body"].read() == data


def test_photo_without_exif_is_processed_and_flagged(aws):
    trial_id, token = create_trial()
    data = jpeg(11, stamp=None)   # no GPS, no time
    _, _, evidence_id = upload(aws, trial_id, token, "photo", data, "image/jpeg")
    item = evidence_of(trial_id, token, evidence_id)
    assert item["state"] == "READY"
    assert item["result"]["exif"]["hasGps"] is False
    assert "no_gps" in item["result"]["exif"]["problems"]


def test_png_is_accepted_and_flagged_as_having_no_gps(aws):
    trial_id, token = create_trial()
    _, _, evidence_id = upload(aws, trial_id, token, "photo", png(2), "image/png")
    item = evidence_of(trial_id, token, evidence_id)
    assert item["state"] == "READY"
    assert "png_without_gps" in item["result"]["exif"]["problems"]


def test_unreadable_image_fails_without_a_model_call(aws):
    trial_id, token = create_trial()
    garbage = b"\xff\xd8\xff\xe0" + b"\x13" * 4000
    _, _, evidence_id = upload(aws, trial_id, token, "photo", garbage, "image/jpeg")
    item = evidence_of(trial_id, token, evidence_id)
    assert item["state"] == "FAILED" and item["error"]["code"] == "UNREADABLE_IMAGE"
    assert item["retryable"] is False
    assert repo.get_trial(trial_id)["bedrockCalls"] == 0


def test_bedrock_failure_keeps_exif_and_can_be_retried(aws, monkeypatch):
    from common import bedrock

    calls = {"n": 0}
    original_check = bedrock.check_photo

    def flaky(image_bytes, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("model unavailable")
        return original_check(image_bytes, **kwargs)

    monkeypatch.setattr(bedrock, "check_photo", flaky)
    trial_id, token = create_trial()
    _, _, evidence_id = upload(aws, trial_id, token, "photo", jpeg(12, lat=22.5, lon=88.4),
                               "image/jpeg")
    item = evidence_of(trial_id, token, evidence_id)
    assert item["state"] == "READY"
    assert item["result"]["exif"]["hasGps"]
    assert item["result"]["vision"]["ran"] is False
    assert item["result"]["vision"]["skippedReason"] == "BEDROCK_FAILED"
    assert item["error"]["code"] == "BEDROCK_FAILED" and item["retryable"]

    status, payload = call(RETRY, token=token, trialId=trial_id, evidenceId=evidence_id)
    assert status == 202, payload
    item = evidence_of(trial_id, token, evidence_id)
    assert item["result"]["vision"]["ran"] is True and item["error"] is None
    assert calls["n"] == 2
    assert repo.get_trial(trial_id)["bedrockCalls"] == 2   # the failed call still counted


def test_retry_is_bounded(aws, monkeypatch):
    from common import bedrock

    monkeypatch.setattr(bedrock, "check_photo",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    trial_id, token = create_trial()
    _, _, evidence_id = upload(aws, trial_id, token, "photo", jpeg(13), "image/jpeg")
    for _ in range(limits.MAX_RETRIES):
        assert call(RETRY, token=token, trialId=trial_id, evidenceId=evidence_id)[0] == 202
    status, payload = call(RETRY, token=token, trialId=trial_id, evidenceId=evidence_id)
    assert status == 409 and payload["code"] == "RETRY_LIMIT"
    assert repo.get_trial(trial_id)["bedrockCalls"] == 1 + limits.MAX_RETRIES


def test_a_ready_photo_with_a_model_result_has_nothing_to_retry(aws):
    trial_id, token = create_trial()
    _, _, evidence_id = upload(aws, trial_id, token, "photo", jpeg(14), "image/jpeg")
    status, payload = call(RETRY, token=token, trialId=trial_id, evidenceId=evidence_id)
    assert status == 409 and payload["code"] == "BAD_STATE"


def test_duplicate_processing_invocation_is_a_no_op(aws, monkeypatch):
    from common import bedrock

    counted = {"n": 0}
    original_check = bedrock.check_photo

    def counting(image_bytes, **kwargs):
        counted["n"] += 1
        return original_check(image_bytes, **kwargs)

    monkeypatch.setattr(bedrock, "check_photo", counting)
    trial_id, token = create_trial()
    _, _, evidence_id = upload(aws, trial_id, token, "photo", jpeg(15), "image/jpeg")
    assert counted["n"] == 1
    # A second delivery of the same async event: the item is READY, not QUEUED.
    assert process.run(trial_id, evidence_id) == "skipped"
    # Even forced back to QUEUED, identical bytes are not sent to the model again.
    repo.update_evidence(trial_id, evidence_id, {"state": "QUEUED"})
    assert process.run(trial_id, evidence_id) == "processed"
    assert counted["n"] == 1


def test_a_live_lease_blocks_a_second_processor(aws):
    trial_id, token = create_trial()
    _, _, evidence_id = upload(aws, trial_id, token, "photo", jpeg(16), "image/jpeg",
                               complete=False)
    repo.update_evidence(trial_id, evidence_id, {"state": "QUEUED"})
    assert repo.claim_for_processing(trial_id, evidence_id) is not None
    assert repo.claim_for_processing(trial_id, evidence_id) is None
    repo.update_evidence(trial_id, evidence_id, {"leaseUntil": repo.now_epoch() - 1})
    assert repo.claim_for_processing(trial_id, evidence_id) is not None


# ------------------------------------------------------------------ quotas
def test_daily_bedrock_allowance_fails_closed(aws, monkeypatch):
    monkeypatch.setenv("TRIAL_DAILY_BEDROCK_CALLS", "1")
    trial_id, token = create_trial()
    _, _, first = upload(aws, trial_id, token, "photo", jpeg(17), "image/jpeg")
    _, _, second = upload(aws, trial_id, token, "photo", jpeg(18), "image/jpeg")
    assert evidence_of(trial_id, token, first)["result"]["vision"]["ran"] is True
    item = evidence_of(trial_id, token, second)
    assert item["result"]["vision"]["ran"] is False
    assert item["result"]["vision"]["skippedReason"] == "QUOTA_EXHAUSTED"
    assert item["error"]["code"] == "QUOTA_EXHAUSTED"
    assert repo.daily_count("BEDROCK") == 1


def test_per_trial_bedrock_allowance(aws, monkeypatch):
    monkeypatch.setenv("TRIAL_MAX_BEDROCK_PER_TRIAL", "1")
    trial_id, token = create_trial()
    upload(aws, trial_id, token, "photo", jpeg(19), "image/jpeg")
    _, _, second = upload(aws, trial_id, token, "photo", jpeg(20), "image/jpeg")
    assert evidence_of(trial_id, token, second)["result"]["vision"]["skippedReason"] == \
        "QUOTA_EXHAUSTED"
    # Another trial still has its own allowance.
    other, other_token = create_trial()
    _, _, third = upload(aws, other, other_token, "photo", jpeg(21), "image/jpeg")
    assert evidence_of(other, other_token, third)["result"]["vision"]["ran"] is True


def test_textract_allowance_fails_closed_without_a_call(aws, monkeypatch):
    from common import textract

    monkeypatch.setenv("TRIAL_DAILY_TEXTRACT_CALLS", "0")
    called = []
    monkeypatch.setattr(textract, "analyze_document", lambda *a, **k: called.append(1))
    trial_id, token = create_trial()
    _, _, evidence_id = upload(aws, trial_id, token, "slip", jpeg(22), "image/jpeg")
    item = evidence_of(trial_id, token, evidence_id)
    assert item["state"] == "FAILED" and item["error"]["code"] == "QUOTA_EXHAUSTED"
    assert called == []


def test_analysis_count_is_limited(aws, monkeypatch):
    monkeypatch.setenv("TRIAL_MAX_ANALYSES", "2")
    trial_id, token = create_trial()
    assert analyze(trial_id, token)[0] == 200
    assert analyze(trial_id, token)[0] == 200
    status, payload = analyze(trial_id, token)
    assert status == 429 and payload["code"] == "ANALYSIS_LIMIT"


# -------------------------------------------------------------------- slips
def test_slip_is_read_with_textract_and_labelled_mock(aws):
    trial_id, token = create_trial()
    _, _, evidence_id = upload(aws, trial_id, token, "slip", jpeg(23), "image/jpeg",
                               filename="slip.jpg")
    item = evidence_of(trial_id, token, evidence_id)
    assert item["state"] == "READY"
    fields = item["result"]["fields"]
    assert fields["net"]["value"] == pytest.approx(9.3)
    assert fields["vehicleNo"]["value"] == "WB 00 MK 0001"
    assert fields["ticketNo"]["value"] == "MOCK-0001"
    assert item["result"]["mocked"] is True
    assert fields["net"]["confidence"] > 0


def test_single_page_pdf_slip_is_accepted(aws):
    trial_id, token = create_trial()
    _, _, evidence_id = upload(aws, trial_id, token, "slip", minimal_pdf(1), "application/pdf")
    assert evidence_of(trial_id, token, evidence_id)["state"] == "READY"


def test_multi_page_pdf_slip_fails_before_textract(aws, monkeypatch):
    from common import textract

    called = []
    monkeypatch.setattr(textract, "analyze_document", lambda *a, **k: called.append(1))
    trial_id, token = create_trial()
    _, _, evidence_id = upload(aws, trial_id, token, "slip", minimal_pdf(3), "application/pdf")
    item = evidence_of(trial_id, token, evidence_id)
    assert item["state"] == "FAILED" and item["error"]["code"] == "PDF_TOO_MANY_PAGES"
    assert called == [] and repo.get_trial(trial_id)["textractCalls"] == 0
    assert item["retryable"] is False


def test_textract_failure_is_stored_and_retryable(aws, monkeypatch):
    from common import textract

    def boom(*args, **kwargs):
        raise RuntimeError("textract down")

    monkeypatch.setattr(textract, "analyze_document", boom)
    trial_id, token = create_trial()
    _, _, evidence_id = upload(aws, trial_id, token, "slip", jpeg(24), "image/jpeg")
    item = evidence_of(trial_id, token, evidence_id)
    assert item["state"] == "FAILED" and item["error"]["code"] == "TEXTRACT_FAILED"
    assert item["retryable"] is True


# ------------------------------------------------------------------- traces
def test_trace_json_and_geojson_are_validated_inline(aws):
    trial_id, token = create_trial()
    points = drive(KOLKATA, DUMP, "2026-10-09T07:00:00+05:30", 20)
    status, payload, evidence_id = upload(aws, trial_id, token, "trace", trace_json(points),
                                          "application/json")
    assert status == 200 and payload["evidence"]["state"] == "READY"
    result = payload["evidence"]["result"]
    assert result["pointCount"] == len(points) and result["vehicleNo"] == "WB 00 MK 0001"

    geojson = {"type": "Feature",
               "properties": {"vehicleNo": "wb-00 mk 0002", "times": [p["t"] for p in points]},
               "geometry": {"type": "LineString",
                            "coordinates": [[p["lon"], p["lat"]] for p in points]}}
    status, payload, _ = upload(aws, trial_id, token, "trace", json.dumps(geojson).encode(),
                                "application/geo+json")
    assert status == 200 and payload["evidence"]["result"]["vehicleNo"] == "WB 00 MK 0002"


def test_invalid_trace_is_rejected_with_a_reason(aws):
    trial_id, token = create_trial()
    points = drive(KOLKATA, DUMP, "2026-10-09T07:00:00+05:30", 20)
    points[3]["t"], points[4]["t"] = points[4]["t"], points[3]["t"]
    status, payload, _ = upload(aws, trial_id, token, "trace", trace_json(points),
                                "application/json")
    assert status == 422 and payload["code"] == "INVALID_TRACE"
    assert "backwards" in payload["error"]


def test_bill_document_is_stored_without_pretending_to_extract_it(aws):
    trial_id, token = create_trial()
    status, payload, _ = upload(aws, trial_id, token, "bill", minimal_pdf(2), "application/pdf")
    assert status == 200
    assert payload["evidence"]["result"]["extraction"] == "not_supported"
    assert repo.get_trial(trial_id)["textractCalls"] == 0


# ----------------------------------------------------------------- details
def test_details_are_validated_server_side(aws):
    trial_id, token = create_trial()
    status, payload = call("PUT /trials/{trialId}/details", token=token, trialId=trial_id,
                           body={"drainLocation": {"point": [200, 22], "source": "official"},
                                 "claim": {"quantityTonnes": -3},
                                 "workWindow": {"start": "2026-10-09T00:00:00+05:30",
                                                "end": "2026-10-01T00:00:00+05:30"},
                                 "owner": "NKDA"})
    assert status == 400 and payload["code"] == "INVALID_DETAILS"
    fields = payload["fields"]
    assert "drainLocation.point" in fields and "drainLocation.source" in fields
    assert "claim.quantityTonnes" in fields and "workWindow.end" in fields
    assert "owner" in fields
    assert repo.get_trial(trial_id)["details"] == {}


def test_details_can_be_set_and_cleared(aws):
    trial_id, token = create_trial()
    details(trial_id, token, truck={"vehicleNo": "wb 00 mk 0001", "capacityTonnes": 10})
    payload = details(trial_id, token, claim={"quantityTonnes": 9.3})
    assert payload["details"]["truck"]["vehicleNo"] == "WB 00 MK 0001"
    assert set(payload["details"]) == {"truck", "claim"}
    payload = details(trial_id, token, truck=None)
    assert set(payload["details"]) == {"claim"}


# ------------------------------------------------------------------ analysis
def test_analysis_waits_for_processing(aws):
    trial_id, token = create_trial()
    _, _, evidence_id = upload(aws, trial_id, token, "photo", jpeg(25), "image/jpeg",
                               complete=False)
    repo.update_evidence(trial_id, evidence_id,
                         {"state": "PROCESSING", "leaseUntil": repo.now_epoch() + 60})
    status, payload = analyze(trial_id, token)
    assert status == 409 and payload["code"] == "EVIDENCE_PROCESSING"
    assert payload["pending"] == [evidence_id]
    assert repo.get_trial(trial_id).get("analysisCount", 0) == 0


def test_only_a_photo_still_gets_photo_analysis_and_nothing_is_invented(aws):
    trial_id, token = create_trial()
    upload(aws, trial_id, token, "photo", jpeg(26, lat=22.58, lon=88.47), "image/jpeg")
    status, result = analyze(trial_id, token)
    assert status == 200
    assert result["observations"][0]["kind"] == "vision"
    statuses = {check["id"]: check["status"] for check in result["checks"]}
    for rule in ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10", "T1", "T2", "T3"):
        assert statuses[rule] == "NOT_EVALUATED", rule
    assert all(check["missing"] for check in result["checks"]
               if check["status"] == "NOT_EVALUATED")
    assert result["counts"]["PASS"] == 0
    assert "Not evaluated is not passed." in result["summary"]["text"]
    assert result["summary"]["text"].startswith("OFFLINE MOCK")


def test_empty_trial_analysis_evaluates_nothing(aws):
    trial_id, token = create_trial()
    status, result = analyze(trial_id, token)
    assert status == 200
    assert {check["status"] for check in result["checks"]} == {"NOT_EVALUATED"}


def test_all_six_groups_end_to_end(aws):
    trial_id, token = create_trial(label="six groups")
    details(trial_id, token,
            drainLocation={"point": list(KOLKATA), "toleranceM": 30, "source": "map_selected",
                           "lengthM": 100, "widthM": 2, "depthM": 1},
            disposalSite={"point": list(DUMP), "radiusM": 200},
            claim={"quantityTonnes": 9.3, "ratePerTonne": 1800, "amountRupees": 16740,
                   "contractor": "Example", "reference": "WO-1"},
            workWindow={"start": "2026-10-01T00:00:00+05:30", "end": "2026-10-10T00:00:00+05:30"},
            truck={"vehicleNo": "WB 00 MK 0001", "capacityTonnes": 10})
    upload(aws, trial_id, token, "bill", minimal_pdf(1), "application/pdf")
    upload(aws, trial_id, token, "photo", jpeg(27, lat=22.5801, lon=88.4712), "image/jpeg",
           role="before")
    upload(aws, trial_id, token, "photo", jpeg(28, lat=22.5802, lon=88.4713), "image/jpeg",
           role="after")
    upload(aws, trial_id, token, "slip", jpeg(29), "image/jpeg")
    points = drive(KOLKATA, DUMP, "2026-10-09T07:00:00+05:30", 25)
    upload(aws, trial_id, token, "trace", trace_json(points), "application/json")

    status, result = analyze(trial_id, token)
    assert status == 200
    by_rule = {}
    for check in result["checks"]:
        by_rule.setdefault(check["id"], []).append(check)

    # Real (non-mock) evidence: supplied details and EXIF-derived checks run.
    assert {c["status"] for c in by_rule["R1"]} == {"CONSISTENT"}
    assert {c["status"] for c in by_rule["R2"]} == {"PASS"}
    assert {c["status"] for c in by_rule["R3"]} == {"PASS"}
    assert by_rule["R5"][0]["status"] == "PASS"
    assert by_rule["GPS_GAP"][0]["status"] == "PASS"
    assert by_rule["R10"][0]["status"] == "PASS"
    assert by_rule["T1"][0]["status"] == "PASS"
    assert by_rule["R6"][0]["status"] == "NOT_EVALUATED"
    # Mock readings never become outcomes, but the logic ran on them.
    for rule in ("R4", "R7", "R8", "R9", "T2", "T3"):
        check = by_rule[rule][0]
        assert check["status"] == "NOT_EVALUATED", rule
        assert check.get("mockOutcome") or check["basis"] is None, rule
    assert by_rule["R8"][0]["mockOutcome"] in ("PASS", "FAIL")
    assert result["provenance"]["mocked"] is True
    assert len(result["evidenceConsidered"]) == 5

    status, again = call("GET /trials/{trialId}/results", token=token, trialId=trial_id)
    assert status == 200 and again["analysisId"] == result["analysisId"]


def test_failed_and_unfinished_uploads_are_listed_as_excluded(aws):
    trial_id, token = create_trial()
    upload(aws, trial_id, token, "photo", b"\xff\xd8\xff" + b"0" * 500, "image/jpeg")
    upload(aws, trial_id, token, "photo", jpeg(30), "image/jpeg", complete=False)
    status, result = analyze(trial_id, token)
    assert status == 200
    states = sorted(item["state"] for item in result["evidenceExcluded"])
    assert states == ["FAILED", "UPLOADING"]


def test_results_before_analysis_is_404(aws):
    trial_id, token = create_trial()
    status, payload = call("GET /trials/{trialId}/results", token=token, trialId=trial_id)
    assert status == 404 and payload["code"] == "NO_ANALYSIS"


def test_field_case_with_photo_derived_geometry_is_inconclusive(aws):
    """The Kolkata field case: same API, real-looking EXIF, circular geometry."""
    trial_id, token = create_trial(kind="field", label="New Town channel")
    details(trial_id, token,
            drainLocation={"line": [[88.4710, 22.5800], [88.4718, 22.5804]],
                           "toleranceM": 40, "source": "field_approximate",
                           "derivedFromPhotos": True},
            workWindow={"start": "2026-10-09T00:00:00+05:30", "end": "2026-10-09T23:59:59+05:30"})
    for seed in (31, 32):
        upload(aws, trial_id, token, "photo",
               jpeg(seed, lat=22.5802, lon=88.4714, model=b"Android\x00\x00"),
               "image/jpeg", role="current")
    _, result = analyze(trial_id, token)
    r1 = checks_by(result, "R1")
    assert {c["status"] for c in r1} == {"INCONCLUSIVE"}
    assert "circular" in r1[0]["message"]
    assert {c["status"] for c in checks_by(result, "R2")} == {"PASS"}
    assert checks_by(result, "R4")[0]["status"] == "NOT_EVALUATED"   # no 'after' label
    assert all("Dhapa" not in check["message"] for check in result["checks"])


# ----------------------------------------------------------- delete/cleanup
def test_delete_evidence_releases_its_slot_once_the_upload_link_is_dead(aws, monkeypatch):
    trial_id, token = create_trial()
    _, _, evidence_id = upload(aws, trial_id, token, "photo", jpeg(33), "image/jpeg")
    later = repo.now_epoch() + limits.UPLOAD_URL_TTL_S + limits.UPLOAD_WINDOW_SLACK_S + 5
    monkeypatch.setattr(repo, "now_epoch", lambda: later)
    status, payload = call("DELETE /trials/{trialId}/evidence/{evidenceId}", token=token,
                           trialId=trial_id, evidenceId=evidence_id)
    assert status == 200 and payload["slotReleased"] is True
    trial = repo.get_trial(trial_id)
    assert trial["fileCount"] == 0 and trial["groupFiles"]["photo"] == 0
    assert get(trial_id, token)["evidence"] == []


def test_delete_while_the_upload_link_lives_keeps_the_slot(aws):
    """Otherwise delete-and-reupload through the still-valid POST policy
    stores files that no counter sees."""
    trial_id, token = create_trial()
    _, _, evidence_id = upload(aws, trial_id, token, "photo", jpeg(33), "image/jpeg")
    status, payload = call("DELETE /trials/{trialId}/evidence/{evidenceId}", token=token,
                           trialId=trial_id, evidenceId=evidence_id)
    assert status == 200 and payload["slotReleased"] is False
    assert repo.get_trial(trial_id)["fileCount"] == 1
    assert get(trial_id, token)["evidence"] == []


def test_delete_trial_removes_every_object_and_record(aws):
    trial_id, token = create_trial()
    other_id, other_token = create_trial()
    upload(aws, trial_id, token, "photo", jpeg(34), "image/jpeg")
    upload(aws, trial_id, token, "slip", jpeg(35), "image/jpeg")
    upload(aws, other_id, other_token, "photo", jpeg(36), "image/jpeg")
    analyze(trial_id, token)

    status, payload = call("DELETE /trials/{trialId}", token=token, trialId=trial_id)
    assert status == 200 and payload["objects"] == 2
    listing = aws["s3"].list_objects_v2(Bucket=aws["bucket"], Prefix=f"trials/{trial_id}/")
    assert "Contents" not in listing
    assert store.query_pk(repo.trial_pk(trial_id)) == []
    assert call("GET /trials/{trialId}", token=token, trialId=trial_id)[0] == 404
    # The other trial is untouched.
    assert len(get(other_id, other_token)["evidence"]) == 1


def test_every_trial_record_expires(aws):
    trial_id, token = create_trial()
    upload(aws, trial_id, token, "photo", jpeg(37), "image/jpeg")
    analyze(trial_id, token)
    expires = repo.get_trial(trial_id)["ttl"]
    for item in store.query_pk(repo.trial_pk(trial_id)):
        assert item["ttl"] == expires, item["sk"]
    day_items = aws["dynamodb"].scan(TableName=aws["table"])["Items"]
    quota = [item for item in day_items if item["pk"]["S"].startswith("QUOTA#")]
    assert quota and all("ttl" in item for item in quota)


# ---------------------------------------------------------------- isolation
def test_trials_never_touch_bill_b1(aws):
    bill = {"pk": "BILL#B1", "sk": "META", "status": "VERIFIED", "claimedTonnes": 1240,
            "summary": {"heldTonnes": 370, "heldRupees": 666000}}
    drain = {"pk": "BILL#B1", "sk": "DRAIN#14", "drainId": "14", "verdict": "RED",
             "heldTonnes": 120}
    evidence = {"pk": "EVID#photos/B1/drain14/after-1.jpg", "sk": "PHOTO", "billId": "B1"}
    store.put_many([bill, drain, evidence])
    aws["s3"].put_object(Bucket=aws["bucket"], Key="photos/B1/drain14/after-1.jpg", Body=b"x")
    before_items = sorted(store.query_pk("BILL#B1"), key=lambda item: item["sk"])

    trial_id, token = create_trial()
    details(trial_id, token, drainLocation={"point": list(KOLKATA)},
            claim={"quantityTonnes": 370, "ratePerTonne": 1800, "amountRupees": 666000})
    upload(aws, trial_id, token, "photo", jpeg(38, lat=22.58, lon=88.47), "image/jpeg")
    upload(aws, trial_id, token, "slip", jpeg(39), "image/jpeg")
    analyze(trial_id, token)
    call("DELETE /trials/{trialId}", token=token, trialId=trial_id)

    assert sorted(store.query_pk("BILL#B1"), key=lambda item: item["sk"]) == before_items
    assert store.get("EVID#photos/B1/drain14/after-1.jpg", "PHOTO") is not None
    assert aws["s3"].get_object(Bucket=aws["bucket"],
                                Key="photos/B1/drain14/after-1.jpg")["Body"].read() == b"x"


def test_trial_writes_stay_in_their_namespace(aws):
    trial_id, token = create_trial()
    upload(aws, trial_id, token, "photo", jpeg(40, lat=22.5, lon=88.4), "image/jpeg")
    upload(aws, trial_id, token, "slip", jpeg(41), "image/jpeg")
    upload(aws, trial_id, token, "trace",
           trace_json(drive(KOLKATA, DUMP, "2026-10-09T07:00:00+05:30", 10)), "application/json")
    analyze(trial_id, token)
    items = aws["dynamodb"].scan(TableName=aws["table"])["Items"]
    assert all(item["pk"]["S"].startswith(("TRIAL#", "QUOTA#")) for item in items)
    keys = [obj["Key"] for obj in
            aws["s3"].list_objects_v2(Bucket=aws["bucket"]).get("Contents", [])]
    assert keys and all(key.startswith(f"trials/{trial_id}/") for key in keys)


def test_trial_uploads_do_not_trigger_b1_ingestion(aws):
    from conftest import s3_event
    from ingest import app as ingest

    result = ingest.lambda_handler(
        s3_event(aws["bucket"], "trials/tr_aaaaaaaaaaaaaaaaaaaaaaaaaa/originals/x.jpg"), None)
    assert result["ignored"] == 1 and result["processed"] == 0


def test_ingest_handles_trial_process_events(aws):
    from ingest import app as ingest

    trial_id, token = create_trial()
    _, _, evidence_id = upload(aws, trial_id, token, "photo", jpeg(42), "image/jpeg",
                               complete=False)
    repo.update_evidence(trial_id, evidence_id, {"state": "QUEUED"})
    result = ingest.lambda_handler(
        {"trialProcess": {"trialId": trial_id, "evidenceId": evidence_id}}, None)
    assert result == {"status": "processed"}
    assert ingest.lambda_handler({"trialProcess": {"trialId": "B1", "evidenceId": "x"}},
                                 None) == {"status": "ignored"}


def test_deployed_dispatch_invokes_the_processor_asynchronously(aws, monkeypatch):
    sent = []

    class FakeLambda:
        def invoke(self, **kwargs):
            sent.append(kwargs)
            return {"StatusCode": 202}

    from common import awsclients

    real_client = awsclients.client
    monkeypatch.setattr(awsclients, "client",
                        lambda name, *a: FakeLambda() if name == "lambda" else real_client(name, *a))
    monkeypatch.setenv("TRIAL_PROCESSOR_FUNCTION", "siltproof-IngestFunction")
    trial_id, token = create_trial()
    status, payload, evidence_id = upload(aws, trial_id, token, "photo", jpeg(43), "image/jpeg")
    assert status == 202 and payload["processing"] == "invoked"
    assert payload["evidence"]["state"] == "QUEUED"
    assert sent[0]["InvocationType"] == "Event"
    assert json.loads(sent[0]["Payload"]) == {
        "trialProcess": {"trialId": trial_id, "evidenceId": evidence_id}}


def test_live_mode_without_a_processor_fails_closed(aws, monkeypatch):
    monkeypatch.setenv("MOCK_AWS", "0")
    monkeypatch.delenv("TRIAL_PROCESSOR_FUNCTION", raising=False)
    monkeypatch.setenv("TRIAL_INVITE_CODE", "judges-river-2026")
    trial_id, token = create_trial(inviteCode="judges-river-2026")
    status, payload, evidence_id = upload(aws, trial_id, token, "photo", jpeg(44), "image/jpeg")
    assert status == 503 and payload["code"] == "PROCESSOR_UNAVAILABLE"
    assert repo.get_evidence(trial_id, evidence_id)["state"] == "UPLOADED"


# ------------------------------------------------------- review hardening
def test_live_mode_without_an_invite_code_keeps_trials_closed(aws, monkeypatch):
    monkeypatch.delenv("TRIAL_INVITE_CODE", raising=False)
    monkeypatch.setenv("MOCK_AWS", "0")
    status, payload = call("POST /trials", body={"label": "x"})
    assert status == 503 and payload["code"] == "TRIALS_DISABLED"
    assert repo.daily_count("TRIALS") == 0


def test_live_mode_with_an_invite_code_opens_trials(aws, monkeypatch):
    monkeypatch.setenv("TRIAL_INVITE_CODE", "judges-river-2026")
    monkeypatch.setenv("MOCK_AWS", "0")
    assert call("POST /trials", body={})[0] == 403
    assert call("POST /trials", body={"inviteCode": "judges-river-2026"})[0] == 201


def test_a_file_replaced_after_complete_is_refused_before_any_model_call(aws, monkeypatch):
    from common import bedrock

    called = {"n": 0}
    real = bedrock.check_photo

    def counting(*args, **kwargs):
        called["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(bedrock, "check_photo", counting)
    trial_id, token = create_trial()
    original = jpeg(40)
    _, _, evidence_id = upload(aws, trial_id, token, "photo", original, "image/jpeg",
                               complete=False)
    item = repo.get_evidence(trial_id, evidence_id)
    # Validated and queued, then overwritten through the still-valid policy.
    repo.update_evidence(trial_id, evidence_id,
                         {"state": "QUEUED", "sizeBytes": len(original)})
    aws["s3"].put_object(Bucket=aws["bucket"], Key=item["s3Key"],
                         Body=b"MZ" + b"\x00" * (len(original) - 2), ContentType="image/jpeg")
    assert process.run(trial_id, evidence_id) == "failed"
    stored = repo.get_evidence(trial_id, evidence_id)
    assert stored["state"] == "FAILED" and stored["error"]["code"] == "CHANGED_AFTER_UPLOAD"
    assert called["n"] == 0
    assert repo.get_trial(trial_id).get("bedrockCalls", 0) == 0


def test_a_photo_over_the_pixel_cap_is_refused_before_decoding(aws, monkeypatch):
    monkeypatch.setattr(limits, "MAX_PHOTO_PIXELS", 320 * 240 - 1)
    trial_id, token = create_trial()
    _, _, evidence_id = upload(aws, trial_id, token, "photo", jpeg(41), "image/jpeg")
    item = evidence_of(trial_id, token, evidence_id)
    assert item["state"] == "FAILED" and item["error"]["code"] == "IMAGE_TOO_LARGE"
    assert item["retryable"] is False
    assert repo.get_trial(trial_id).get("bedrockCalls", 0) == 0


def test_phone_originals_go_to_bedrock_as_a_copy():
    """Converse takes at most 3.75 MB per image; 3-8 MB phone originals need a copy."""
    assert limits.MAX_BYTES_FOR_BEDROCK <= 3_750_000


def _stall(trial_id, evidence_id, state):
    if state == "PROCESSING":
        repo.update_evidence(trial_id, evidence_id,
                             {"state": "PROCESSING", "leaseUntil": repo.now_epoch() - 1})
    else:
        repo.update_evidence(trial_id, evidence_id,
                             {"state": "QUEUED", "updatedAt": "2026-01-01T00:00:00+00:00"})


@pytest.mark.parametrize("state", ["PROCESSING", "QUEUED"])
def test_a_stalled_file_does_not_block_analysis_and_can_be_retried_or_removed(aws, state):
    trial_id, token = create_trial()
    _, _, stuck = upload(aws, trial_id, token, "photo", jpeg(42), "image/jpeg", complete=False)
    repo.update_evidence(trial_id, stuck, {"sizeBytes": len(jpeg(42))})
    _stall(trial_id, stuck, state)

    status, result = analyze(trial_id, token)
    assert status == 200
    assert [item["evidenceId"] for item in result["evidenceExcluded"]] == [stuck]
    assert "did not finish" in result["evidenceExcluded"][0]["reason"]

    assert evidence_of(trial_id, token, stuck)["retryable"] is True
    status, payload = call(RETRY, token=token, trialId=trial_id, evidenceId=stuck)
    assert status == 202, payload
    assert evidence_of(trial_id, token, stuck)["state"] == "READY"

    _, _, other = upload(aws, trial_id, token, "photo", jpeg(43), "image/jpeg", complete=False)
    _stall(trial_id, other, state)
    status, _ = call("DELETE /trials/{trialId}/evidence/{evidenceId}", token=token,
                     trialId=trial_id, evidenceId=other)
    assert status == 200


def test_a_live_lease_still_blocks_removal(aws):
    trial_id, token = create_trial()
    _, _, evidence_id = upload(aws, trial_id, token, "photo", jpeg(44), "image/jpeg",
                               complete=False)
    repo.update_evidence(trial_id, evidence_id,
                         {"state": "PROCESSING", "leaseUntil": repo.now_epoch() + 60})
    status, payload = call("DELETE /trials/{trialId}/evidence/{evidenceId}", token=token,
                           trialId=trial_id, evidenceId=evidence_id)
    assert status == 409 and payload["code"] == "BAD_STATE"
