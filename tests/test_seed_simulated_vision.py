"""`seed.py --simulated-vision` and `check_seed.py`, end to end in moto.

The generated photos are stand-ins, so a real model cannot be trusted to
reproduce the verdicts the generator planted (R4 decides drain 3's 126 t).
In this mode each photo's planned verdict travels as S3 metadata and ingest
stores it as simulated, with no Bedrock call; slips still go through the
Textract path. The seed check must then pass and the rules must reproduce
ground_truth.json exactly. Mock Textract returns the printed values here, so
this proves the plumbing and the arithmetic, not real Textract accuracy.
"""

import json

import pytest

import check_seed
import dataset as ds
import gen_photos
import gen_slips
import gen_trips
import osm_drains
import seed as seed_script

CENTER = "19.0760,72.8777"


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    import boto3
    from moto import mock_aws as moto_mock

    from conftest import BUCKET, TABLE

    patch = pytest.MonkeyPatch()
    out = tmp_path_factory.mktemp("simulated")
    for name, value in (
        ("AWS_ACCESS_KEY_ID", "testing"), ("AWS_SECRET_ACCESS_KEY", "testing"),
        ("AWS_SESSION_TOKEN", "testing"), ("AWS_DEFAULT_REGION", "ap-south-1"),
        ("AWS_REGION", "ap-south-1"), ("MOCK_AWS", "1"), ("TABLE_NAME", TABLE),
        ("EVIDENCE_BUCKET", BUCKET), ("BILL_ID", "B1"),
    ):
        patch.setenv(name, value)
    patch.setattr(ds, "OUT", out)

    from common import bedrock

    calls = {"bedrock": 0}

    def no_model(*args, **kwargs):
        calls["bedrock"] += 1
        raise AssertionError("simulated vision must never call Bedrock")

    patch.setattr(bedrock, "check_photo", no_model)

    with moto_mock():
        s3 = boto3.client("s3", region_name="ap-south-1")
        s3.create_bucket(Bucket=BUCKET,
                         CreateBucketConfiguration={"LocationConstraint": "ap-south-1"})
        boto3.client("dynamodb", region_name="ap-south-1").create_table(
            TableName=TABLE,
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                  {"AttributeName": "sk", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                       {"AttributeName": "sk", "KeyType": "RANGE"}],
            BillingMode="PAY_PER_REQUEST",
        )
        from common import awsclients, store

        awsclients.reset_cache()
        assert osm_drains.main(["--synthetic", "--center", CENTER]) == 0
        assert gen_trips.main([]) == 0
        assert gen_slips.main([]) == 0
        assert gen_photos.main([]) == 0
        patch.setenv("MOCK_MANIFEST_PATH", str(out / "mock_manifest.json"))

        assert seed_script.main(["--live", "--yes", "--simulated-vision",
                                 "--bucket", BUCKET, "--table", TABLE]) == 0

        import ingest.app as ingest_app

        keys = []
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET):
            keys.extend(obj["Key"] for obj in page.get("Contents", []) or [])
        for start in range(0, len(keys), 25):
            ingest_app.lambda_handler({"Records": [
                {"s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}
                for key in keys[start:start + 25]]}, None)

        manifest = ds.load_json(out / "mock_manifest.json")
        trips = ds.load_json(out / "trips.json")["trips"]
        yield {
            "s3": s3, "bucket": BUCKET, "store": store, "calls": calls, "manifest": manifest,
            "traces": sorted(trip["traceKey"] for trip in trips),
            "truth": ds.load_json(out / "ground_truth.json"),
        }
        awsclients.reset_cache()
    patch.undo()


def read(seeded):
    store = seeded["store"]
    return lambda key, kind: store.get(store.evidence_pk(key), kind)


def test_photos_carry_their_planned_verdict_and_no_model_is_called(seeded):
    head = seeded["s3"].head_object(Bucket=seeded["bucket"], Key="photos/B1/drain3/after-01.jpg")
    assert json.loads(head["Metadata"]["sp-sim-vision"])["loadType"] in ("silt", "debris", "unclear")
    assert seeded["calls"]["bedrock"] == 0
    for key, planned in seeded["manifest"]["photos"].items():
        vision = read(seeded)(key, "PHOTO")["bedrock"]
        assert vision["simulated"] is True and vision["modelId"] is None and vision["mocked"] is False
        assert (vision["cleared"], vision["load_type"]) == (planned["cleared"], planned["loadType"])


def test_the_seed_check_passes_and_the_rules_reproduce_the_ground_truth(seeded):
    report = check_seed.compare(seeded["manifest"], read(seeded), seeded["traces"])
    assert report["blocking"] == []
    assert report["checked"] == {"slips": 117, "photos": 40, "traces": 117}

    status, bill = check_seed.verify_in_process("B1")
    assert status == 200
    ok, detail = check_seed.totals_match(bill["summary"], seeded["truth"]["totals"])
    assert ok, detail
    assert bill["summary"]["heldRupees"] == 666000


def test_a_misread_slip_or_a_real_model_verdict_blocks_verification(seeded):
    store = seeded["store"]
    slip = "slips/B1/14-001.png"
    item = store.get(store.evidence_pk(slip), "SLIP")
    photo = "photos/B1/drain3/after-01.jpg"
    shot = store.get(store.evidence_pk(photo), "PHOTO")
    try:
        store.put({**item, "net": float(item["net"]) + 1})
        store.put({**shot, "bedrock": {**shot["bedrock"], "simulated": False,
                                        "modelId": "apac.amazon.nova-pro-v1:0"}})
        blocking = check_seed.compare(seeded["manifest"], read(seeded), seeded["traces"])["blocking"]
        assert any(line.startswith(f"{slip}: net") for line in blocking)
        assert any(line.startswith(f"{photo}: not stored as simulated") for line in blocking)
    finally:
        store.put(item)
        store.put(shot)


def test_simulated_vision_is_ignored_for_another_bill(seeded):
    from ingest import app as ingest_app

    obj = {"metadata": {"sp-sim-vision": '{"cleared":true,"loadType":"silt"}'}}
    assert ingest_app.simulated_verdict(obj, {"billId": "OTHER"}) is None
    bad = ingest_app.simulated_verdict({"metadata": {"sp-sim-vision": "{"}}, {"billId": "B1"})
    assert bad["ok"] is False and bad["simulated"] is True   # never falls through to Bedrock
