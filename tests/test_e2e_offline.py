"""The Day 1 exit check, run entirely offline.

    synthetic drains -> trips and traces -> slips -> photos
      -> seed (into moto S3 and DynamoDB)
      -> invoke the ingest handler on every uploaded object
      -> assert every photo, slip and trace has extracted facts in DynamoDB

The plan's Day 1 exit check is "every photo and slip in S3 has extracted facts
in DynamoDB". This is that check with S3 and DynamoDB mocked by moto and the AI
calls mocked through MOCK_AWS=1, so it can run with no AWS account at all.
"""

import pytest

import dataset as ds
import gen_photos
import gen_slips
import gen_trips
import osm_drains
import seed as seed_script
from common import geo, store

CENTER = "19.0760,72.8777"
SLIP_LIMIT = 12          # enough to cover the planted drains; keeps the test quick


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    """Generate the dataset, seed it into moto, and ingest every object.

    Module-scoped on purpose: generating 40 photos, 117 traces and the slips
    takes about half a minute, and every assertion below reads the same
    finished state.
    """
    from moto import mock_aws as moto_mock

    import boto3

    from conftest import BUCKET, TABLE

    patch = pytest.MonkeyPatch()
    out = tmp_path_factory.mktemp("e2e")

    patch.setenv("AWS_ACCESS_KEY_ID", "testing")
    patch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    patch.setenv("AWS_SESSION_TOKEN", "testing")
    patch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
    patch.setenv("AWS_REGION", "ap-south-1")
    patch.setenv("MOCK_AWS", "1")
    patch.setenv("TABLE_NAME", TABLE)
    patch.setenv("EVIDENCE_BUCKET", BUCKET)
    patch.setenv("BILL_ID", "B1")
    patch.setattr(ds, "OUT", out)

    with moto_mock():
        s3 = boto3.client("s3", region_name="ap-south-1")
        s3.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": "ap-south-1"},
        )
        boto3.client("dynamodb", region_name="ap-south-1").create_table(
            TableName=TABLE,
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        from common import awsclients

        awsclients.reset_cache()

        assert osm_drains.main(["--synthetic", "--center", CENTER]) == 0
        assert gen_trips.main([]) == 0
        assert gen_slips.main(["--limit", str(SLIP_LIMIT)]) == 0
        assert gen_photos.main([]) == 0

        # The mocked providers now answer with what the generators actually drew.
        patch.setenv("MOCK_MANIFEST_PATH", str(out / "mock_manifest.json"))

        assert seed_script.main(
            ["--live", "--yes", "--bucket", BUCKET, "--table", TABLE]
        ) == 0

        # moto does not fire S3 notifications, so drive the handler ourselves -
        # exactly what the deployed stack does on each ObjectCreated event.
        import ingest.app as ingest_app

        keys = []
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=BUCKET):
            keys.extend(obj["Key"] for obj in page.get("Contents", []) or [])

        totals = {"processed": 0, "skipped": 0, "ignored": 0, "failed": 0}
        for start_index in range(0, len(keys), 10):
            event = {
                "Records": [
                    {"s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}
                    for key in keys[start_index:start_index + 10]
                ]
            }
            result = ingest_app.lambda_handler(event, None)
            for name in totals:
                totals[name] += result[name]

        yield {
            "bucket": BUCKET,
            "keys": keys,
            "totals": totals,
            "out": out,
            "trips": ds.load_json(out / "trips.json")["trips"],
            "truth": ds.load_json(out / "ground_truth.json"),
            "drains": ds.load_json(out / "drains.geojson"),
            "manifest": ds.load_json(out / "mock_manifest.json"),
        }

        awsclients.reset_cache()

    patch.undo()


# --------------------------------------------------------------- exit check
def test_every_object_ingested_without_failures(seeded):
    assert seeded["totals"]["failed"] == 0
    assert seeded["totals"]["ignored"] == 0
    assert seeded["totals"]["processed"] == len(seeded["keys"])


def test_every_photo_has_extracted_facts(seeded):
    photos = [key for key in seeded["keys"] if key.startswith("photos/")]
    assert len(photos) == 40

    for key in photos:
        item = store.get(store.evidence_pk(key), "PHOTO")
        assert item is not None, key
        assert item["status"] == "OK"
        assert item["pHash"], key
        assert item["hasGps"] is True, key
        assert item["hasTimestamp"] is True, key
        assert item["bedrock"]["load_type"] in ("silt", "debris", "unclear")


def test_every_slip_has_extracted_facts(seeded):
    slips = [key for key in seeded["keys"] if key.startswith("slips/")]
    assert len(slips) == SLIP_LIMIT

    for key in slips:
        item = store.get(store.evidence_pk(key), "SLIP")
        assert item is not None, key
        assert item["status"] == "OK"
        assert item["net"] is not None, key
        assert item["vehicleNo"] is not None, key
        assert item["textract"]["confidenceAvg"] > 0


def test_every_trace_has_a_summary(seeded):
    traces = [key for key in seeded["keys"] if key.startswith("traces/")]
    assert len(traces) == len(seeded["trips"])

    for key in traces:
        item = store.get(store.evidence_pk(key), "TRACE")
        assert item is not None, key
        assert item["pointCount"] > 2, key
        assert item["startTime"] and item["endTime"]


def test_the_extracted_slip_matches_what_the_slip_says(seeded):
    """The mock is manifest-driven, so this really compares the two sides."""
    slips = [key for key in seeded["keys"] if key.startswith("slips/")]

    for key in slips:
        printed = seeded["manifest"]["slips"][key]
        item = store.get(store.evidence_pk(key), "SLIP")
        assert item["net"] == pytest.approx(float(printed["net"]), abs=0.01), key
        assert item["vehicleNo"] == printed["vehicleNo"], key


def test_the_bill_drains_and_trips_are_in_dynamodb(seeded):
    items = store.query_pk(store.bill_pk("B1"))
    kinds = {}
    for item in items:
        kind = item["sk"].split("#")[0]
        kinds[kind] = kinds.get(kind, 0) + 1

    assert kinds["DRAIN"] == 18
    assert kinds["TRIP"] == len(seeded["trips"])
    assert kinds["PHASH"] == 40          # one index entry per photo
    assert store.get(store.bill_pk("B1"), "META")["claimedTonnes"] == 1240


def test_vehicles_and_the_dump_site_are_in_dynamodb(seeded):
    for vehicle in ds.vehicles():
        item = store.get(store.vehicle_pk(vehicle["vehicleNo"]), "META")
        assert item["capacityTonnes"] == vehicle["capacityTonnes"]

    dumpsite = store.get(store.dumpsite_pk("D1"), "META")
    assert dumpsite["geofence"]["type"] == "Polygon"


# --------------------------------------- the planted cases survive the trip
def test_r3_the_reused_photo_is_findable_by_phash(seeded):
    """Drain 14's after photos hash to drain 9's, which is what R3 looks for."""
    source = store.get(store.evidence_pk("photos/B1/drain9/after-01.jpg"), "PHOTO")
    exact = store.get(store.evidence_pk("photos/B1/drain14/after-02.jpg"), "PHOTO")
    edited = store.get(store.evidence_pk("photos/B1/drain14/after-03.jpg"), "PHOTO")

    from common import photo

    assert photo.hamming(source["pHash"], exact["pHash"]) == 0
    assert photo.hamming(source["pHash"], edited["pHash"]) <= ds.PHASH_DUPLICATE_MAX

    # The index a rule would actually query.
    index = store.query_pk(store.bill_pk("B1"), f"PHASH#{source['pHash']}#")
    assert len(index) == 2          # drain 9's original and drain 14's exact copy


def test_r4_drain_3_load_is_debris_and_drain_9_is_silt(seeded):
    debris = store.get(store.evidence_pk("photos/B1/drain3/load-01.jpg"), "PHOTO")
    silt = store.get(store.evidence_pk("photos/B1/drain9/load-01.jpg"), "PHOTO")

    assert debris["bedrock"]["load_type"] == "debris"
    assert silt["bedrock"]["load_type"] == "silt"


def test_r1_drain_8_photo_sits_in_the_soft_band(seeded):
    item = store.get(store.evidence_pk("photos/B1/drain8/after-01.jpg"), "PHOTO")
    feature = next(
        f for f in seeded["drains"]["features"] if f["properties"]["drainId"] == "8"
    )

    point = [item["lon"], item["lat"]]
    distance = geo.distance_to_line_m(point, feature["properties"]["centreline"])

    assert not geo.point_in_geometry(point, feature["geometry"])
    assert ds.DRAIN_BUFFER_M < distance <= 60, distance


def test_r5_drain_14_traces_never_enter_the_dump_site(seeded):
    dumpsite = store.get(store.dumpsite_pk("D1"), "META")

    for trip in [t for t in seeded["trips"] if t["drainId"] == "14"]:
        trace = ds.load_json(seeded["out"] / "evidence" / trip["traceKey"])
        assert not any(
            geo.point_in_geometry([p["lon"], p["lat"]], dumpsite["geofence"])
            for p in trace["points"]
        )


def test_r7_the_overloaded_slips_beat_their_vehicle_capacity(seeded):
    flagged = [
        trip for trip in seeded["trips"]
        if trip["drainId"] == "11" and trip["marker"] == "R7_OVERLOAD"
    ]
    assert flagged

    for trip in flagged:
        capacity = store.get(store.vehicle_pk(trip["vehicleNo"]), "META")["capacityTonnes"]
        assert trip["slip"]["net"] > capacity


def test_gps_gap_is_visible_in_the_trace_summary(seeded):
    trip = next(
        t for t in seeded["trips"] if t["drainId"] == "6" and t["marker"] == "GPS_GAP"
    )
    item = store.get(store.evidence_pk(trip["traceKey"]), "TRACE")

    assert item["maxGapSeconds"] > 180


def test_ground_truth_is_stored_with_the_data(seeded):
    item = store.get(store.bill_pk("B1"), "GROUNDTRUTH")

    assert item["totals"]["heldRupees"] == 666000
    assert item["drains"]["14"]["expectedColour"] == "RED"


# ------------------------------------------------------------- re-ingestion
def test_a_second_pass_costs_nothing(seeded):
    """Re-running the whole ingest skips everything: no repeat AI spend."""
    import ingest.app as ingest_app

    event = {
        "Records": [
            {"s3": {"bucket": {"name": seeded["bucket"]}, "object": {"key": key}}}
            for key in seeded["keys"][:20]
        ]
    }
    result = ingest_app.lambda_handler(event, None)

    assert result["skipped"] == 20
    assert result["processed"] == 0
