"""The ingest Lambda, against moto S3 + DynamoDB with the providers mocked."""

import datetime
import io
import json

import piexif
import pytest
from PIL import Image

from conftest import s3_event

import gen_photos


def put(aws, key, body, content_type="application/octet-stream"):
    aws["s3"].put_object(Bucket=aws["bucket"], Key=key, Body=body, ContentType=content_type)


def a_photo(lat=19.0760, lon=72.8777, when=None, colour=(100, 120, 110)):
    stamp = when or datetime.datetime(2026, 9, 28, 9, 42, 11)
    buffer = io.BytesIO()
    Image.new("RGB", (320, 240), colour).save(buffer, format="JPEG", quality=88)

    out = io.BytesIO()
    piexif.insert(gen_photos.exif_bytes(lat, lon, stamp), buffer.getvalue(), out)
    return out.getvalue()


def a_trace(points):
    return json.dumps(
        {"tripId": "14#001", "drainId": "14", "tripNo": "001",
         "vehicleNo": "MH 01 AB 1234", "points": points}
    ).encode()


@pytest.fixture
def handler(aws):
    import ingest.app as app

    return app


# ------------------------------------------------------------------ photos
def test_a_photo_becomes_an_evidence_item(aws, handler):
    from common import store

    key = "photos/B1/drain14/after-01.jpg"
    put(aws, key, a_photo(), "image/jpeg")

    result = handler.lambda_handler(s3_event(aws["bucket"], key), None)
    assert result["processed"] == 1

    item = store.get(store.evidence_pk(key), "PHOTO")
    assert item is not None
    assert item["status"] == "OK"
    assert item["drainId"] == "14"
    assert item["role"] == "after"
    assert item["hasGps"] is True
    assert item["lat"] == pytest.approx(19.0760, abs=1e-4)
    assert item["timestamp"].startswith("2026-09-28T09:42:11")
    assert item["pHash"]
    assert item["bedrock"]["load_type"] in ("silt", "debris", "unclear")
    assert item["bedrock"]["ok"] is True
    # A canned mock answer must not be attributed to a Bedrock model.
    assert item["bedrock"]["mocked"] is True
    assert item["bedrock"]["modelId"] is None


def test_a_photo_also_lands_in_the_phash_index(aws, handler):
    """Rule R3 finds duplicates with one query instead of a table scan."""
    from common import store

    key = "photos/B1/drain9/after-01.jpg"
    put(aws, key, a_photo(), "image/jpeg")
    handler.lambda_handler(s3_event(aws["bucket"], key), None)

    index = store.query_pk(store.bill_pk("B1"), "PHASH#")
    assert len(index) == 1
    assert index[0]["s3Key"] == key
    assert index[0]["drainId"] == "9"


def test_a_photo_with_no_exif_still_ingests(aws, handler):
    from common import store

    key = "photos/B1/drain2/before-01.jpg"
    buffer = io.BytesIO()
    Image.new("RGB", (200, 150), (70, 70, 70)).save(buffer, format="JPEG")
    put(aws, key, buffer.getvalue(), "image/jpeg")

    handler.lambda_handler(s3_event(aws["bucket"], key), None)

    item = store.get(store.evidence_pk(key), "PHOTO")
    assert item["status"] == "OK"
    assert item["hasGps"] is False
    assert "no_exif" in item["problems"]
    assert item["pHash"]            # hashing still worked


# ------------------------------------------------------------------- slips
def test_a_slip_becomes_an_evidence_item(aws, handler):
    from common import store

    key = "slips/B1/14-001.png"
    put(aws, key, b"pretend png bytes", "image/png")

    handler.lambda_handler(s3_event(aws["bucket"], key), None)

    item = store.get(store.evidence_pk(key), "SLIP")
    assert item["status"] == "OK"
    assert item["drainId"] == "14"
    assert item["tripNo"] == "001"
    assert item["net"] == 9.8
    assert item["vehicleNo"] == "MH 01 AB 1234"
    assert item["timeIn"] == "09:42"
    assert item["textract"]["confidenceAvg"] > 0


def test_a_degraded_slip_records_its_missing_fields(aws, handler, tmp_path, monkeypatch):
    from common import store

    key = "slips/B1/3-005.png"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"slips": {key: {"augmented": True, "net": "10.60 T"}}}))
    monkeypatch.setenv("MOCK_MANIFEST_PATH", str(manifest))

    put(aws, key, b"pretend png bytes", "image/png")
    handler.lambda_handler(s3_event(aws["bucket"], key), None)

    item = store.get(store.evidence_pk(key), "SLIP")
    assert item["net"] == 10.6
    assert item["timeOut"] is None
    assert "missing:timeOut" in item["problems"]


# ------------------------------------------------------------------ traces
def test_a_trace_is_summarised(aws, handler):
    from common import store

    start = datetime.datetime(2026, 9, 28, 9, 0, 0)
    points = [
        {"t": (start + datetime.timedelta(seconds=30 * i)).isoformat(),
         "lat": 19.07 + i * 0.001, "lon": 72.87 + i * 0.001}
        for i in range(10)
    ]
    # A four-minute hole in the middle, like the drain 6 case.
    points = points[:5] + [
        {"t": (start + datetime.timedelta(seconds=30 * 5 + 240)).isoformat(),
         "lat": 19.08, "lon": 72.88}
    ]

    key = "traces/B1/6-006.json"
    put(aws, key, a_trace(points), "application/json")
    handler.lambda_handler(s3_event(aws["bucket"], key), None)

    item = store.get(store.evidence_pk(key), "TRACE")
    assert item["status"] == "OK"
    assert item["drainId"] == "6"
    assert item["tripNo"] == "006"
    assert item["pointCount"] == 6
    assert item["maxGapSeconds"] == 270
    assert item["vehicleNo"] == "MH 01 AB 1234"
    assert item["startPoint"] and item["endPoint"]


def test_an_unparseable_trace_is_recorded_not_dropped(aws, handler):
    from common import store

    key = "traces/B1/1-001.json"
    put(aws, key, b"{not json at all", "application/json")
    handler.lambda_handler(s3_event(aws["bucket"], key), None)

    item = store.get(store.evidence_pk(key), "TRACE")
    assert item["pointCount"] == 0
    assert any(problem.startswith("unparseable_trace") for problem in item["problems"])


# -------------------------------------------------------- routing and reruns
def test_an_unknown_prefix_is_ignored(aws, handler):
    put(aws, "notes/readme.txt", b"hello")

    result = handler.lambda_handler(s3_event(aws["bucket"], "notes/readme.txt"), None)

    assert result == {"processed": 0, "skipped": 0, "ignored": 1, "failed": 0, "received": 1}


def test_reprocessing_the_same_object_is_skipped(aws, handler):
    """A re-seed must not pay for Bedrock and Textract all over again."""
    key = "photos/B1/drain1/after-01.jpg"
    put(aws, key, a_photo(), "image/jpeg")

    first = handler.lambda_handler(s3_event(aws["bucket"], key), None)
    second = handler.lambda_handler(s3_event(aws["bucket"], key), None)

    assert first["processed"] == 1
    assert second["skipped"] == 1
    assert second["processed"] == 0


def test_a_changed_object_is_processed_again(aws, handler):
    key = "photos/B1/drain1/after-01.jpg"
    put(aws, key, a_photo(colour=(100, 120, 110)), "image/jpeg")
    handler.lambda_handler(s3_event(aws["bucket"], key), None)

    put(aws, key, a_photo(colour=(30, 60, 90)), "image/jpeg")
    again = handler.lambda_handler(s3_event(aws["bucket"], key), None)

    assert again["processed"] == 1


def test_force_reingest_overrides_the_skip(aws, handler, monkeypatch):
    key = "photos/B1/drain1/after-01.jpg"
    put(aws, key, a_photo(), "image/jpeg")
    handler.lambda_handler(s3_event(aws["bucket"], key), None)

    monkeypatch.setenv("FORCE_REINGEST", "1")
    again = handler.lambda_handler(s3_event(aws["bucket"], key), None)

    assert again["processed"] == 1


def test_ingestion_is_idempotent(aws, handler):
    """Running twice leaves exactly one item, with the same content."""
    from common import store

    key = "slips/B1/7-002.png"
    put(aws, key, b"pretend png bytes", "image/png")

    handler.lambda_handler(s3_event(aws["bucket"], key), None)
    first = store.get(store.evidence_pk(key), "SLIP")

    import os

    os.environ["FORCE_REINGEST"] = "1"
    try:
        handler.lambda_handler(s3_event(aws["bucket"], key), None)
    finally:
        del os.environ["FORCE_REINGEST"]

    second = store.get(store.evidence_pk(key), "SLIP")

    assert first["net"] == second["net"]
    assert first["pk"] == second["pk"] and first["sk"] == second["sk"]


def test_a_missing_object_is_recorded_as_an_error(aws, handler):
    from common import store

    key = "photos/B1/drain1/after-99.jpg"   # never uploaded

    result = handler.lambda_handler(s3_event(aws["bucket"], key), None)

    assert result["failed"] == 1
    item = store.get(store.evidence_pk(key), "PHOTO")
    assert item["status"] == "ERROR"
    assert "NoSuchKey" in item["error"]


def test_one_bad_record_does_not_stop_the_others(aws, handler):
    good = "slips/B1/2-001.png"
    put(aws, good, b"pretend png bytes", "image/png")

    result = handler.lambda_handler(
        s3_event(aws["bucket"], "photos/B1/drain1/missing.jpg", good), None
    )

    assert result["processed"] == 1
    assert result["failed"] == 1


def test_url_encoded_keys_are_decoded(aws, handler):
    from common import store

    key = "photos/B1/drain5/after-01.jpg"
    put(aws, key, a_photo(), "image/jpeg")

    event = s3_event(aws["bucket"], "photos/B1/drain5/after-01.jpg")
    event["Records"][0]["s3"]["object"]["key"] = "photos/B1/drain5/after-01.jpg"
    handler.lambda_handler(event, None)

    assert store.get(store.evidence_pk(key), "PHOTO") is not None


@pytest.mark.parametrize(
    "key,kind,drain,trip_or_role",
    [
        ("photos/B1/drain14/after-02.jpg", "PHOTO", "14", "after"),
        ("photos/B1/drain3/load-01.jpg", "PHOTO", "3", "load"),
        ("slips/B1/16-004.png", "SLIP", "16", "004"),
        ("traces/B1/6-006.json", "TRACE", "6", "006"),
    ],
)
def test_key_convention_is_parsed(key, kind, drain, trip_or_role, aws, handler):
    parsed = handler.parse_evidence_key(key, kind)

    assert parsed["billId"] == "B1"
    assert parsed["drainId"] == drain
    assert (parsed["role"] or parsed["tripNo"]) == trip_or_role


def test_an_off_convention_key_still_ingests(aws, handler):
    """A live demo upload has no drain in its key, and must still work."""
    from common import store

    key = "photos/live/whatever-1234.jpg"
    put(aws, key, a_photo(), "image/jpeg")

    handler.lambda_handler(s3_event(aws["bucket"], key), None)

    item = store.get(store.evidence_pk(key), "PHOTO")
    assert item["status"] == "OK"
    assert item["drainId"] is None
