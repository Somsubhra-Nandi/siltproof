"""The HTTP API: routing, validation, decisions, caching and missing evidence.

Built on a small hand-made bill rather than the full dataset, so these stay
fast and test the API's own behaviour. The rules themselves are judged against
the real dataset in test_rules_oracle.py.
"""

import json

import pytest

BILL = "B1"


def call(api_app, route_key, *, path=None, body=None, query=None):
    event = {"routeKey": route_key}
    if path:
        event["pathParameters"] = path
    if body is not None:
        event["body"] = json.dumps(body)
    if query:
        event["queryStringParameters"] = query

    result = api_app.lambda_handler(event, None)
    return result["statusCode"], json.loads(result["body"])


@pytest.fixture
def api(aws):
    import api.app as api_app

    return api_app


@pytest.fixture
def small_bill(aws):
    """Two drains, three trips, with matching evidence in S3 and DynamoDB.

    Drain 1 is clean. Drain 2's single trip has a slip claiming more than its
    truck can carry, so it must come out red on R7.
    """
    import datetime

    from common import store

    begin = datetime.datetime.fromisoformat("2026-09-25T10:00:00+05:30")

    def trace(key, arrive=True):
        finish = [72.9320, 19.1270] if arrive else [72.9000, 19.1000]
        points = [
            {
                "t": (begin + datetime.timedelta(seconds=30 * index)).isoformat(),
                "lon": 72.8710 + (finish[0] - 72.8710) * index / 20,
                "lat": 19.0702 + (finish[1] - 19.0702) * index / 20,
            }
            for index in range(21)
        ]
        aws["s3"].put_object(
            Bucket=aws["bucket"], Key=key,
            Body=json.dumps({"points": points}).encode(), ContentType="application/json",
        )
        return points

    geofence = {
        "type": "Polygon",
        "coordinates": [[
            [72.8700, 19.0700], [72.8719, 19.0700],
            [72.8719, 19.0705], [72.8700, 19.0705], [72.8700, 19.0700],
        ]],
    }

    items = [
        {
            "pk": store.bill_pk(BILL), "sk": "META", "billId": BILL,
            "contractor": "Simulated Contractor Pvt Ltd", "ward": "Test ward",
            "ratePerTonne": 1800, "claimedTonnes": 30, "claimedRupees": 54000,
            "workWindowStart": "2026-09-21T06:00:00+05:30",
            "workWindowEnd": "2026-10-04T19:00:00+05:30",
            "status": "PENDING", "simulated": True,
        },
        {
            "pk": store.dumpsite_pk("D1"), "sk": "META", "dumpsiteId": "D1",
            "name": "Approved dumping ground", "center": [72.9320, 19.1270],
            "geofence": {
                "type": "Polygon",
                "coordinates": [[
                    [72.9300, 19.1250], [72.9340, 19.1250],
                    [72.9340, 19.1290], [72.9300, 19.1290], [72.9300, 19.1250],
                ]],
            },
        },
        {"pk": store.vehicle_pk("MH 01 AA 1000"), "sk": "META",
         "vehicleNo": "MH 01 AA 1000", "capacityTonnes": 16},
        {"pk": store.vehicle_pk("MH 01 AB 1137"), "sk": "META",
         "vehicleNo": "MH 01 AB 1137", "capacityTonnes": 14},
        {"pk": store.vehicle_pk("MH 01 AD 1411"), "sk": "META",
         "vehicleNo": "MH 01 AD 1411", "capacityTonnes": 10},
    ]

    for drain_id, claimed in (("1", 20), ("2", 10)):
        items.append(
            {
                "pk": store.bill_pk(BILL), "sk": store.drain_sk(drain_id),
                "drainId": drain_id, "name": f"Drain section {drain_id}",
                "lengthM": 200.0, "widthM": 2.0, "depthM": 1.2,
                "plausibleMaxTonnes": 806.4, "claimedTonnes": claimed,
                "claimedRupees": claimed * 1800, "geofence": geofence,
                "centreline": [[72.8700, 19.07025], [72.8719, 19.07025]],
                "verdict": None, "decision": None, "note": None,
            }
        )

    # One truck per trip: two simultaneous trips by one truck is exactly what
    # R6 exists to catch, and these three are meant to be otherwise clean.
    trips = [
        ("1", "001", "MH 01 AA 1000", 10.0, 10.0),
        ("1", "002", "MH 01 AB 1137", 10.0, 10.0),
        ("2", "001", "MH 01 AD 1411", 10.0, 14.0),     # 14 t on a 10 t truck
    ]

    for drain_id, trip_no, vehicle, claimed, net in trips:
        slip_key = f"slips/{BILL}/{drain_id}-{trip_no}.png"
        trace_key = f"traces/{BILL}/{drain_id}-{trip_no}.json"
        points = trace(trace_key)

        items.append(
            {
                "pk": store.bill_pk(BILL), "sk": store.trip_sk(drain_id, trip_no),
                "tripId": f"{drain_id}#{trip_no}", "drainId": drain_id, "tripNo": trip_no,
                "vehicleNo": vehicle, "claimedTonnes": claimed,
                "slipKey": slip_key, "traceKey": trace_key,
                "startTime": points[0]["t"], "arrivalTime": points[-1]["t"],
                "endTime": points[-1]["t"], "verdict": None, "failedRules": [],
            }
        )
        items.append(
            {
                "pk": store.evidence_pk(slip_key), "sk": "SLIP", "s3Key": slip_key,
                "billId": BILL, "drainId": drain_id, "tripNo": trip_no,
                "status": "OK", "vehicleNo": vehicle, "net": net,
                "gross": net + 12.6, "tare": 12.6,
                "timeIn": "10:10", "timeOut": "10:30",
                "textract": {"confidenceAvg": 97.0, "fields": {},
                             "missingFields": [], "lowConfidenceFields": []},
            }
        )
        items.append(
            {
                "pk": store.evidence_pk(trace_key), "sk": "TRACE", "s3Key": trace_key,
                "billId": BILL, "drainId": drain_id, "tripNo": trip_no,
                "status": "OK", "pointCount": len(points), "maxGapSeconds": 30,
            }
        )

    # Hashes at least 32 bits apart. An earlier version of this fixture built
    # them from the drain id and role, which left neighbouring photos one bit
    # apart - and R3 rightly called them all copies of each other.
    hashes = {
        ("1", "before"): "0000000000000000",
        ("1", "after"): "ffffffffffffffff",
        ("2", "before"): "0f0f0f0f0f0f0f0f",
        ("2", "after"): "f0f0f0f0f0f0f0f0",
    }

    for drain_id in ("1", "2"):
        for role in ("before", "after"):
            key = f"photos/{BILL}/drain{drain_id}/{role}-01.jpg"
            items.append(
                {
                    "pk": store.evidence_pk(key), "sk": "PHOTO", "s3Key": key,
                    "billId": BILL, "drainId": drain_id, "role": role, "status": "OK",
                    "hasGps": True, "lat": 19.07025, "lon": 72.8710,
                    "timestamp": "2026-09-25T09:00:00+05:30",
                    "pHash": hashes[(drain_id, role)],
                    "problems": [],
                    "bedrock": {"cleared": role == "after", "load_type": "unclear",
                                "confidence": 0.9, "notes": "", "ok": True,
                                "modelId": "mock"},
                }
            )

    store.put_many(items)
    return {"trips": trips}


# ------------------------------------------------------------------ health
def test_health_reports_the_configuration(api):
    status, body = call(api, "GET /health")

    assert status == 200
    assert body["status"] == "ok"
    assert body["mockAws"] is True
    assert body["bedrockVisionModelId"]


# ----------------------------------------------------------------- routing
def test_an_unknown_route_is_404(api):
    status, body = call(api, "DELETE /everything")

    assert status == 404
    assert "No route" in body["error"]


def test_a_malformed_body_is_400(api):
    result = api.lambda_handler(
        {"routeKey": "POST /decision", "body": "{not json"}, None
    )
    assert result["statusCode"] == 400


def test_a_non_object_body_is_400(api):
    status, _ = call(api, "POST /decision", body=[1, 2, 3])
    assert status == 400


def test_every_response_allows_the_browser_in(api):
    result = api.lambda_handler({"routeKey": "GET /health"}, None)
    assert result["headers"]["Access-Control-Allow-Origin"] == "*"


# -------------------------------------------------------------------- bill
def test_an_unknown_bill_is_404(api, small_bill):
    status, body = call(api, "GET /bill/{billId}", path={"billId": "NOPE"})

    assert status == 404
    assert "No bill" in body["error"]


def test_an_unverified_bill_holds_nothing_yet(api, small_bill):
    status, body = call(api, "GET /bill/{billId}", path={"billId": BILL})

    assert status == 200
    assert body["status"] == "PENDING"
    assert body["summary"]["claimedTonnes"] == 30
    assert body["summary"]["verifiedTonnes"] == 0
    assert body["summary"]["heldTonnes"] == 0
    assert body["summary"]["pendingTonnes"] == 30
    assert all(drain["verdict"] is None for drain in body["drains"])


def test_verify_then_get_bill_agree(api, small_bill):
    status, verified = call(api, "POST /verify/{billId}", path={"billId": BILL})
    assert status == 200

    status, fetched = call(api, "GET /bill/{billId}", path={"billId": BILL})

    assert fetched["status"] == "VERIFIED"
    assert fetched["summary"]["heldTonnes"] == verified["summary"]["heldTonnes"]
    assert fetched["summary"]["verifiedTonnes"] == verified["summary"]["verifiedTonnes"]
    assert fetched["verifiedAt"]


def test_verification_holds_the_overloaded_drain(api, small_bill):
    status, body = call(api, "POST /verify/{billId}", path={"billId": BILL})

    rows = {drain["drainId"]: drain for drain in body["drains"]}
    assert rows["1"]["verdict"] == "GREEN"
    assert rows["2"]["verdict"] == "RED"
    assert rows["2"]["failedRules"] == []          # the failure is on the trip
    assert body["summary"]["heldTonnes"] == 10
    assert body["summary"]["verifiedTonnes"] == 20


def test_verify_on_a_bill_that_was_never_seeded(api):
    status, body = call(api, "POST /verify/{billId}", path={"billId": "B1"})

    assert status == 404
    assert "seed" in body["error"]


def test_the_rule_texts_travel_with_the_response(api, small_bill):
    _, body = call(api, "POST /verify/{billId}", path={"billId": BILL})

    assert body["rules"]["R7"].startswith("Slip net weight")
    assert "R1" in body["rules"]


# ------------------------------------------------------------------- drain
def test_an_unknown_drain_is_404(api, small_bill):
    status, body = call(api, "GET /drain/{drainId}", path={"drainId": "99"})

    assert status == 404


def test_the_drill_down_carries_evidence_and_both_routes(api, small_bill):
    call(api, "POST /verify/{billId}", path={"billId": BILL})
    status, drain = call(api, "GET /drain/{drainId}", path={"drainId": "2"})

    assert status == 200
    assert drain["verdict"] == "RED"
    assert len(drain["photos"]) == 2
    assert drain["photos"][0]["bedrock"]["loadType"] == "unclear"

    trip = drain["trips"][0]
    assert trip["slip"]["net"] == 14
    assert trip["hardFails"] == ["R7"]
    assert trip["findings"][0]["message"]
    assert len(drain["claimedRoute"]) == 2
    assert len(trip["actualRoute"]) > 2
    assert drain["dumpsite"]["center"]


def test_the_drill_down_works_before_verification(api, small_bill):
    status, drain = call(api, "GET /drain/{drainId}", path={"drainId": "1"})

    assert status == 200
    assert drain["verdict"] is None
    assert len(drain["trips"]) == 2


# ---------------------------------------------------------------- decision
def test_a_decision_needs_a_drain(api, small_bill):
    status, body = call(api, "POST /decision", body={"decision": "APPROVE"})

    assert status == 400
    assert "drainId" in body["error"]


def test_a_decision_must_be_approve_or_hold(api, small_bill):
    status, body = call(
        api, "POST /decision", body={"drainId": "1", "decision": "maybe"}
    )
    assert status == 400


def test_a_note_must_be_text(api, small_bill):
    call(api, "POST /verify/{billId}", path={"billId": BILL})
    status, _ = call(
        api, "POST /decision",
        body={"drainId": "1", "decision": "APPROVE", "note": {"not": "text"}},
    )
    assert status == 400


def test_deciding_before_verifying_is_refused(api, small_bill):
    status, body = call(
        api, "POST /decision", body={"drainId": "1", "decision": "APPROVE"}
    )

    assert status == 409
    assert "verification" in body["error"].lower()


def test_an_unknown_drain_cannot_be_decided(api, small_bill):
    call(api, "POST /verify/{billId}", path={"billId": BILL})
    status, _ = call(
        api, "POST /decision", body={"drainId": "99", "decision": "HOLD"}
    )
    assert status == 404


def test_holding_a_drain_is_recorded_with_its_note(api, small_bill):
    call(api, "POST /verify/{billId}", path={"billId": BILL})
    status, body = call(
        api, "POST /decision",
        body={"drainId": "2", "decision": "HOLD", "note": "Slip weight impossible."},
    )

    assert status == 200
    assert body["decision"] == "HOLD"
    assert body["note"] == "Slip weight impossible."
    assert body["decidedAt"]
    assert body["summary"]["heldTonnes"] == 10


def test_approving_releases_the_money_and_persists(api, small_bill):
    call(api, "POST /verify/{billId}", path={"billId": BILL})
    _, decided = call(
        api, "POST /decision", body={"drainId": "2", "decision": "APPROVE"}
    )

    assert decided["summary"]["heldTonnes"] == 0
    assert decided["summary"]["verifiedTonnes"] == 30

    _, bill = call(api, "GET /bill/{billId}", path={"billId": BILL})
    assert bill["summary"]["verifiedTonnes"] == 30
    assert bill["summary"]["decided"] == 1


def test_a_long_note_is_trimmed(api, small_bill):
    call(api, "POST /verify/{billId}", path={"billId": BILL})
    _, body = call(
        api, "POST /decision",
        body={"drainId": "1", "decision": "APPROVE", "note": "x" * 900},
    )

    assert len(body["note"]) == 500


def test_a_decision_can_be_changed(api, small_bill):
    call(api, "POST /verify/{billId}", path={"billId": BILL})
    call(api, "POST /decision", body={"drainId": "2", "decision": "APPROVE"})
    _, body = call(api, "POST /decision", body={"drainId": "2", "decision": "HOLD"})

    assert body["summary"]["heldTonnes"] == 10


# ----------------------------------------------------------------- summary
def test_the_summary_is_written_by_bedrock_then_cached(api, small_bill):
    call(api, "POST /verify/{billId}", path={"billId": BILL})

    status, first = call(
        api, "POST /drain/{drainId}/summary", path={"drainId": "2"}, body={}
    )
    assert status == 200
    assert first["cached"] is False
    assert first["summary"]
    assert first["basedOnRules"] == ["R7"]

    status, second = call(
        api, "POST /drain/{drainId}/summary", path={"drainId": "2"}, body={}
    )
    assert second["cached"] is True
    assert second["summary"] == first["summary"]


def test_refresh_bypasses_the_cache(api, small_bill):
    call(api, "POST /verify/{billId}", path={"billId": BILL})
    call(api, "POST /drain/{drainId}/summary", path={"drainId": "2"}, body={})

    _, refreshed = call(
        api, "POST /drain/{drainId}/summary", path={"drainId": "2"},
        body={"refresh": True},
    )
    assert refreshed["cached"] is False


def test_a_clean_drain_says_so_without_calling_a_model(api, small_bill):
    call(api, "POST /verify/{billId}", path={"billId": BILL})
    _, body = call(
        api, "POST /drain/{drainId}/summary", path={"drainId": "1"}, body={}
    )

    assert body["modelId"] is None
    assert "passed" in body["summary"]


def test_a_summary_before_verification_is_refused(api, small_bill):
    status, body = call(
        api, "POST /drain/{drainId}/summary", path={"drainId": "1"}, body={}
    )
    assert status == 409


def test_a_model_failure_surfaces_as_502(api, small_bill, monkeypatch):
    call(api, "POST /verify/{billId}", path={"billId": BILL})

    from common import bedrock

    def explode(*args, **kwargs):
        raise RuntimeError("Bedrock is down")

    monkeypatch.setattr(bedrock, "summarise", explode)

    status, body = call(
        api, "POST /drain/{drainId}/summary", path={"drainId": "2"}, body={}
    )
    assert status == 502
    assert "summary" in body["error"]


# --------------------------------------------------------------- uploading
def test_an_upload_url_is_presigned_under_live(api, small_bill):
    status, body = call(
        api, "POST /upload-url", body={"prefix": "slips", "filename": "ticket.png"}
    )

    assert status == 200
    assert body["key"].startswith("slips/live/")
    assert body["key"].endswith(".png")
    assert "X-Amz-Signature" in body["uploadUrl"]
    assert body["expiresInSeconds"] == 900


def test_live_uploads_are_off_in_a_live_deployment_unless_enabled(api, small_bill, monkeypatch):
    monkeypatch.setenv("MOCK_AWS", "0")
    monkeypatch.delenv("B1_OPERATOR_ROUTES", raising=False)
    status, body = call(api, "POST /upload-url", body={"prefix": "photos"})
    assert status == 403 and "uploadUrl" not in body

    monkeypatch.setenv("B1_OPERATOR_ROUTES", "enabled")
    status, body = call(api, "POST /upload-url", body={"prefix": "photos"})
    assert status == 200 and body["key"].startswith("photos/live/")


def test_summary_refresh_is_off_in_a_live_deployment_but_the_cache_still_serves(
        api, small_bill, monkeypatch):
    call(api, "POST /verify/{billId}", path={"billId": BILL})
    call(api, "POST /drain/{drainId}/summary", path={"drainId": "2"}, body={})
    monkeypatch.setenv("MOCK_AWS", "0")
    monkeypatch.delenv("B1_OPERATOR_ROUTES", raising=False)

    status, _ = call(api, "POST /drain/{drainId}/summary", path={"drainId": "2"},
                     body={"refresh": True})
    assert status == 403
    status, cached = call(api, "POST /drain/{drainId}/summary", path={"drainId": "2"},
                          body={})
    assert status == 200 and cached["cached"] is True


def test_an_unknown_upload_prefix_is_refused(api, small_bill):
    status, _ = call(api, "POST /upload-url", body={"prefix": "../etc"})
    assert status == 400


def test_an_odd_filename_falls_back_to_jpg(api, small_bill):
    _, body = call(api, "POST /upload-url", body={"filename": "no-extension"})
    assert body["key"].endswith(".jpg")


# --------------------------------------------------------- missing evidence
def test_a_trip_with_no_slip_goes_to_review_not_to_hold(api, small_bill):
    from common import store

    store.table().delete_item(
        Key={"pk": store.evidence_pk(f"slips/{BILL}/1-001.png"), "sk": "SLIP"}
    )

    _, body = call(api, "POST /verify/{billId}", path={"billId": BILL})

    assert body["missingEvidence"]["tripsWithoutSlip"] == 1
    rows = {drain["drainId"]: drain for drain in body["drains"]}
    assert rows["1"]["verdict"] == "AMBER"
    assert rows["1"]["reviewTonnes"] == 10
    assert rows["1"]["heldTonnes"] == 0


def test_a_trace_missing_from_s3_is_reported_not_ignored(api, small_bill):
    aws_s3 = __import__("boto3").client("s3", region_name="ap-south-1")
    import os

    aws_s3.delete_object(
        Bucket=os.environ["EVIDENCE_BUCKET"], Key=f"traces/{BILL}/1-002.json"
    )

    _, body = call(api, "POST /verify/{billId}", path={"billId": BILL})

    assert body["missingEvidence"]["unreadableTraces"] == 1
    rows = {drain["drainId"]: drain for drain in body["drains"]}
    assert rows["1"]["verdict"] == "AMBER"


def test_an_ingestion_error_is_surfaced(api, small_bill):
    from common import store

    key = f"photos/{BILL}/drain1/after-01.jpg"
    store.put(
        {
            "pk": store.evidence_pk(key), "sk": "PHOTO", "s3Key": key,
            "billId": BILL, "drainId": "1", "status": "ERROR",
            "error": "NoSuchKey: the object vanished",
        }
    )

    _, body = call(api, "POST /verify/{billId}", path={"billId": BILL})

    assert body["missingEvidence"]["evidenceErrors"] == 1
    rows = {drain["drainId"]: drain for drain in body["drains"]}
    assert rows["1"]["verdict"] == "AMBER"
    assert "EVIDENCE_ERROR" in rows["1"]["failedRules"]


def test_a_drain_with_no_photos_at_all_is_flagged(api, small_bill):
    from common import store

    for role in ("before", "after"):
        store.table().delete_item(
            Key={"pk": store.evidence_pk(f"photos/{BILL}/drain1/{role}-01.jpg"),
                 "sk": "PHOTO"}
        )

    _, body = call(api, "POST /verify/{billId}", path={"billId": BILL})

    assert body["missingEvidence"]["drainsWithoutPhotos"] == 1
    rows = {drain["drainId"]: drain for drain in body["drains"]}
    assert "PHOTOS_MISSING" in rows["1"]["failedRules"]


# ------------------------------------------------- evidence image links
DRAIN_FIELDS_BEFORE_IMAGE_LINKS = {
    "billId", "drainId", "name", "verdict", "decision", "note", "decidedAt",
    "claimedTonnes", "verifiedTonnes", "reviewTonnes", "heldTonnes", "lengthM",
    "widthM", "depthM", "plausibleMaxTonnes", "geofence", "claimedRoute", "dumpsite",
    "findings", "failedRules", "summary", "summaryModelId", "photos", "trips", "rules",
}


def signed_key(url):
    """The object key and expiry a presigned GET link points at."""
    from urllib.parse import parse_qs, unquote, urlparse

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    key = unquote(parsed.path.lstrip("/"))
    if key.startswith(BUCKET_NAME + "/"):          # path-style addressing
        key = key[len(BUCKET_NAME) + 1:]
    return key, int(query["X-Amz-Expires"][0])


BUCKET_NAME = "siltproof-test-evidence"


def test_the_drill_down_links_its_own_images_for_five_minutes(api, small_bill):
    status, drain = call(api, "GET /drain/{drainId}", path={"drainId": "2"})

    assert status == 200
    assert drain["evidenceUrlExpiresInSeconds"] == 300
    for photo in drain["photos"]:
        key, expires = signed_key(photo["imageUrl"])
        assert key == photo["s3Key"]
        assert expires == 300
        assert BUCKET_NAME in photo["imageUrl"]

    trip = drain["trips"][0]
    key, expires = signed_key(trip["slipImageUrl"])
    assert key == trip["slipKey"] == "slips/B1/2-001.png"
    assert expires == 300


def test_image_links_are_additive(api, small_bill):
    _, drain = call(api, "GET /drain/{drainId}", path={"drainId": "1"})

    assert DRAIN_FIELDS_BEFORE_IMAGE_LINKS <= set(drain)
    assert {"s3Key", "role", "bedrock", "pHash"} <= set(drain["photos"][0])
    assert {"slip", "slipKey", "traceKey", "actualRoute"} <= set(drain["trips"][0])
    assert drain["photos"][0]["s3Key"] == "photos/B1/drain1/after-01.jpg"


def test_a_missing_slip_gets_no_link(api, small_bill):
    from common import store

    store.table().delete_item(Key={"pk": store.evidence_pk("slips/B1/2-001.png"), "sk": "SLIP"})
    status, drain = call(api, "GET /drain/{drainId}", path={"drainId": "2"})

    assert status == 200
    assert drain["trips"][0]["slip"] is None
    assert drain["trips"][0]["slipImageUrl"] is None


def test_no_bucket_means_no_links(api, small_bill, monkeypatch):
    monkeypatch.setenv("EVIDENCE_BUCKET", "")
    status, drain = call(api, "GET /drain/{drainId}", path={"drainId": "2"})

    assert status == 200
    assert all(photo["imageUrl"] is None for photo in drain["photos"])
    assert drain["trips"][0]["slipImageUrl"] is None


@pytest.mark.parametrize(
    "key, kind",
    [
        ("photos/B1/drain2/after-01.jpg", "photo"),
        ("photos/B1/drain2/before-01.png", "photo"),
        ("slips/B1/2-001.png", "slip"),
    ],
)
def test_canonical_keys_are_accepted(api, key, kind):
    assert api.evidence_key_ok(key, kind, "B1", "2")


@pytest.mark.parametrize(
    "key, kind",
    [
        ("photos/B1/drain2/../drain1/after-01.jpg", "photo"),   # traversal
        ("photos/B1/drain2/after-01.jpg/../../x", "photo"),
        ("/photos/B1/drain2/after-01.jpg", "photo"),             # absolute
        ("photos/B1/drain22/after-01.jpg", "photo"),             # another drain
        ("photos/B1/drain1/after-01.jpg", "photo"),
        ("photos/B2/drain2/after-01.jpg", "photo"),              # another bill
        ("photos/live/0f0f.jpg", "photo"),                       # unattached upload
        ("photos/B1/drain2/selfie-01.jpg", "photo"),             # unknown role
        ("photos/B1/drain2/after-01.html", "photo"),             # not an image
        ("traces/B1/2-001.json", "photo"),                       # wrong prefix
        ("traces/B1/2-001.json", "slip"),
        ("slips/B1/2-001.png", "photo"),                         # wrong kind
        ("slips/B1/12-001.png", "slip"),
        ("slips/B1/2-01.png", "slip"),
        ("slips/B1/2-001.png ", "slip"),
        ("", "slip"),
        (None, "slip"),
        ("slips/B1/2-001.png", "trace"),
    ],
)
def test_malformed_or_foreign_keys_are_refused(api, key, kind):
    assert not api.evidence_key_ok(key, kind, "B1", "2")


@pytest.mark.parametrize("bill_id, drain_id", [("B1/..", "2"), ("B1", "2/.."), ("", "2"), ("B1", None)])
def test_bad_ids_are_refused(api, bill_id, drain_id):
    assert not api.evidence_key_ok("slips/B1/2-001.png", "slip", bill_id, drain_id)


def test_an_item_filed_under_the_wrong_drain_gets_no_link(api, small_bill):
    """A photo item claiming drain 2 but keyed to drain 1, or to another bill,
    is listed (it is evidence the engineer should know about) but unsigned."""
    from common import store

    for key in ("photos/B1/drain1/load-07.jpg", "photos/B2/drain2/after-09.jpg"):
        store.put({
            "pk": store.evidence_pk(key), "sk": "PHOTO", "s3Key": key,
            "billId": BILL, "drainId": "2", "role": "after", "status": "OK",
            "pHash": "1234123412341234", "problems": [], "bedrock": {},
        })

    _, drain = call(api, "GET /drain/{drainId}", path={"drainId": "2"})
    links = {photo["s3Key"]: photo["imageUrl"] for photo in drain["photos"]}

    assert links["photos/B1/drain1/load-07.jpg"] is None
    assert links["photos/B2/drain2/after-09.jpg"] is None
    assert links["photos/B1/drain2/after-01.jpg"] is not None


def test_a_slip_item_owned_by_another_drain_gets_no_link(api, small_bill):
    from common import store

    store.update_fields(store.evidence_pk("slips/B1/2-001.png"), "SLIP", {"drainId": "1"})
    _, drain = call(api, "GET /drain/{drainId}", path={"drainId": "2"})

    assert drain["trips"][0]["slipImageUrl"] is None


def test_image_links_are_never_logged_or_stored(api, small_bill, capsys):
    from common import store

    call(api, "POST /verify/{billId}", path={"billId": BILL})
    _, drain = call(api, "GET /drain/{drainId}", path={"drainId": "2"})
    assert drain["photos"][0]["imageUrl"]

    assert "X-Amz-Signature" not in capsys.readouterr().out
    items = store.table().scan()["Items"]
    assert "X-Amz-" not in json.dumps(items, default=str)


def test_offline_summaries_name_no_model_and_describe_their_own_drain(api, small_bill):
    call(api, "POST /verify/{billId}", path={"billId": BILL})
    _, body = call(api, "POST /drain/{drainId}/summary", path={"drainId": "2"}, body={})

    assert body["modelId"] is None
    assert body["summary"].startswith("Offline summary, no model was called.")
    assert "14.00 t on a truck rated 10 t" in body["summary"]
    assert "drain 14" not in body["summary"]
