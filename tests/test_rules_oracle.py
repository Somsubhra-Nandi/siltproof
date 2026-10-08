"""Rules R1-R10 judged against data/out/ground_truth.json.

The oracle is produced by the data generators, which know what they planted;
the rules know nothing about it and work only from what ingestion wrote into
DynamoDB and S3. If the two agree on all 117 trips, the rules are reading real
evidence rather than reproducing a script.

Nothing is hard-coded here: every assertion compares the engine's output to
the generated file.
"""

import json

import pytest

import dataset as ds
import gen_photos
import gen_slips
import gen_trips
import osm_drains
import seed as seed_script

CENTER = "19.0760,72.8777"


@pytest.fixture(scope="module")
def verified(tmp_path_factory):
    """Full dataset -> moto -> ingest -> POST /verify. Runs once."""
    import boto3
    from moto import mock_aws as moto_mock

    from conftest import BUCKET, TABLE

    patch = pytest.MonkeyPatch()
    out = tmp_path_factory.mktemp("oracle")

    for name, value in (
        ("AWS_ACCESS_KEY_ID", "testing"),
        ("AWS_SECRET_ACCESS_KEY", "testing"),
        ("AWS_SESSION_TOKEN", "testing"),
        ("AWS_DEFAULT_REGION", "ap-south-1"),
        ("AWS_REGION", "ap-south-1"),
        ("MOCK_AWS", "1"),
        ("TABLE_NAME", TABLE),
        ("EVIDENCE_BUCKET", BUCKET),
        ("BILL_ID", "B1"),
    ):
        patch.setenv(name, value)
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
        assert gen_slips.main([]) == 0            # every trip gets a slip
        assert gen_photos.main([]) == 0

        patch.setenv("MOCK_MANIFEST_PATH", str(out / "mock_manifest.json"))

        assert seed_script.main(
            ["--live", "--yes", "--bucket", BUCKET, "--table", TABLE]
        ) == 0

        import ingest.app as ingest_app

        keys = []
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET):
            keys.extend(obj["Key"] for obj in page.get("Contents", []) or [])

        for start in range(0, len(keys), 25):
            ingest_app.lambda_handler(
                {
                    "Records": [
                        {"s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}
                        for key in keys[start:start + 25]
                    ]
                },
                None,
            )

        import api.app as api_app

        result = api_app.lambda_handler(
            {"routeKey": "POST /verify/{billId}", "pathParameters": {"billId": "B1"}}, None
        )

        yield {
            "api": api_app,
            "out": out,
            "keys": keys,
            "status": result["statusCode"],
            "body": json.loads(result["body"]),
            "truth": ds.load_json(out / "ground_truth.json"),
        }

        awsclients.reset_cache()

    patch.undo()


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


# ------------------------------------------------------------- the oracle
def test_verification_succeeds(verified):
    assert verified["status"] == 200
    assert verified["body"]["tripsChecked"] == len(verified["truth"]["trips"])


def test_every_trip_matches_the_oracle(verified):
    """Verdict and rule ids, trip by trip, against what the generator planted."""
    from common import store

    mismatches = []
    for expected in verified["truth"]["trips"]:
        drain_id, trip_no = expected["drainId"], expected["tripNo"]
        item = store.get(store.bill_pk("B1"), store.trip_sk(drain_id, trip_no))

        assert item is not None, expected["tripId"]

        actual = {
            "verdict": item.get("verdict"),
            "hard": sorted(item.get("hardFails") or []),
            "soft": sorted(item.get("softFails") or []),
        }
        wanted = {
            "verdict": expected["expectedVerdict"],
            "hard": sorted(expected["hardFails"]),
            "soft": sorted(expected["softFails"]),
        }

        if actual != wanted:
            mismatches.append((expected["tripId"], wanted, actual))

    assert not mismatches, "\n".join(
        f"{trip_id}: expected {wanted}, got {actual}" for trip_id, wanted, actual in mismatches[:15]
    )


def test_every_drain_colour_matches_the_oracle(verified):
    rows = {row["drainId"]: row for row in verified["body"]["drains"]}

    for drain_id, expected in verified["truth"]["drains"].items():
        assert rows[drain_id]["verdict"] == expected["expectedColour"], drain_id


def test_drain_money_matches_the_oracle(verified):
    rows = {row["drainId"]: row for row in verified["body"]["drains"]}

    for drain_id, expected in verified["truth"]["drains"].items():
        row = rows[drain_id]
        assert row["claimedTonnes"] == pytest.approx(expected["claimedTonnes"], abs=0.05), drain_id
        assert row["verifiedTonnes"] == pytest.approx(expected["verifiedTonnes"], abs=0.05), drain_id
        assert row["reviewTonnes"] == pytest.approx(expected["reviewTonnes"], abs=0.05), drain_id
        assert row["heldTonnes"] == pytest.approx(expected["heldTonnes"], abs=0.05), drain_id


def test_the_bill_totals_are_the_plan_headline(verified):
    summary = verified["body"]["summary"]
    totals = verified["truth"]["totals"]

    assert summary["claimedTonnes"] == pytest.approx(totals["claimedTonnes"], abs=0.05)
    assert summary["verifiedTonnes"] == pytest.approx(totals["verifiedTonnes"], abs=0.05)
    assert summary["reviewTonnes"] == pytest.approx(totals["reviewTonnes"], abs=0.05)
    assert summary["heldTonnes"] == pytest.approx(totals["heldTonnes"], abs=0.05)
    assert summary["heldRupees"] == totals["heldRupees"]            # Rs 6.66 lakh
    assert summary["claimedRupees"] == 2232000                      # Rs 22.32 lakh


def test_no_evidence_is_missing(verified):
    gaps = verified["body"]["missingEvidence"]

    assert gaps == {
        "tripsWithoutSlip": 0,
        "tripsWithoutTrace": 0,
        "unreadableTraces": 0,
        "evidenceErrors": 0,
        "drainsWithoutPhotos": 0,
    }


def test_verification_is_fast(verified):
    """It runs on camera, so it has to be rules-only and quick."""
    assert verified["body"]["verificationMs"] < 20_000


def test_rerunning_verification_is_stable(verified):
    """Idempotent: the same evidence must give the same answer."""
    first = verified["body"]["summary"]
    status, again = call(
        verified["api"], "POST /verify/{billId}", path={"billId": "B1"}
    )

    assert status == 200
    for field in ("claimedTonnes", "verifiedTonnes", "reviewTonnes", "heldTonnes"):
        assert again["summary"][field] == first[field]


# ------------------------------------------------- the planted cases, named
def test_drain_14_is_held_for_the_three_reasons_in_the_plan(verified):
    status, drain = call(verified["api"], "GET /drain/{drainId}", path={"drainId": "14"})

    assert status == 200
    assert drain["verdict"] == "RED"

    rules_hit = {rule for trip in drain["trips"] for rule in trip["hardFails"]}
    assert rules_hit == {"R3", "R5", "R8"}

    messages = " ".join(
        finding["message"] for trip in drain["trips"] for finding in trip["findings"]
    )
    assert "never enters the approved dump site" in messages
    assert "before the truck left the drain" in messages
    assert "never reaches the dump site; its last fix" in messages
    assert "same image as" in messages


def test_drain_3_is_held_because_the_load_is_debris(verified):
    status, drain = call(verified["api"], "GET /drain/{drainId}", path={"drainId": "3"})

    assert drain["verdict"] == "RED"
    assert drain["failedRules"] == ["R4"]
    assert any(photo["bedrock"]["loadType"] == "debris" for photo in drain["photos"])


def test_drain_11_is_held_on_two_overloaded_slips(verified):
    status, drain = call(verified["api"], "GET /drain/{drainId}", path={"drainId": "11"})

    held = [trip for trip in drain["trips"] if trip["verdict"] == "HOLD"]
    assert len(held) == 2
    for trip in held:
        assert trip["hardFails"] == ["R7"]
        assert trip["slip"]["net"] > 10


def test_drain_16_is_held_for_an_impossible_turnaround(verified):
    status, drain = call(verified["api"], "GET /drain/{drainId}", path={"drainId": "16"})

    held = [trip for trip in drain["trips"] if trip["verdict"] == "HOLD"]
    assert {rule for trip in held for rule in trip["hardFails"]} >= {"R6"}

    speeds = [
        finding["evidence"]["impliedSpeedKmh"]
        for trip in held
        for finding in trip["findings"]
        if finding["rule"] == "R6"
    ]
    assert max(speeds) > 40


def test_drain_6_is_amber_for_one_honest_gps_gap(verified):
    status, drain = call(verified["api"], "GET /drain/{drainId}", path={"drainId": "6"})

    assert drain["verdict"] == "AMBER"
    review = [trip for trip in drain["trips"] if trip["verdict"] == "REVIEW"]
    assert len(review) == 1
    assert review[0]["softFails"] == ["GPS_GAP"]
    assert review[0]["hardFails"] == []


def test_drain_8_is_amber_from_the_r1_soft_band(verified):
    """The agreed reading: 30-60 m out is a question, not proof."""
    status, drain = call(verified["api"], "GET /drain/{drainId}", path={"drainId": "8"})

    assert drain["verdict"] == "AMBER"
    assert drain["failedRules"] == ["R1"]

    soft = [f for f in drain["findings"] if f["rule"] == "R1"]
    assert soft and all(f["severity"] == "soft" for f in soft)
    assert 30 < soft[0]["evidence"]["distanceM"] <= 60


def test_the_clean_drains_really_are_clean(verified):
    clean = set(ds.DRAIN_CLAIMED) - set(ds.PLANTED)
    rows = {row["drainId"]: row for row in verified["body"]["drains"]}

    for drain_id in clean:
        assert rows[drain_id]["verdict"] == "GREEN", drain_id
        assert rows[drain_id]["failedRules"] == [], drain_id
        assert rows[drain_id]["heldTonnes"] == 0


def test_r3_names_the_photo_that_was_copied(verified):
    status, drain = call(verified["api"], "GET /drain/{drainId}", path={"drainId": "14"})

    r3 = [f for f in drain["findings"] if f["rule"] == "R3"]
    assert len(r3) == 2                      # the exact copy and the edited one

    for finding in r3:
        assert finding["evidence"]["originalDrainId"] == "9"
        assert finding["evidence"]["hammingDistance"] <= 12


def test_the_drill_down_has_both_routes(verified):
    status, drain = call(verified["api"], "GET /drain/{drainId}", path={"drainId": "14"})

    assert len(drain["claimedRoute"]) == 2            # drain -> approved dump site
    trip = drain["trips"][0]
    assert len(trip["actualRoute"]) > 5
    assert trip["actualRouteDistanceM"] > 0

    # The hero case: the actual route stops short of where the claim ends.
    from common import geo

    end_of_claim = drain["claimedRoute"][-1]
    end_of_actual = trip["actualRoute"][-1]
    assert geo.haversine_m(end_of_claim, end_of_actual) > 1000


# ------------------------------------------------- the engineer's decisions
def test_approving_the_review_drains_reaches_the_plan_headline(verified):
    """Plan section 6: 870 t verified, Rs 6.66 lakh still held.

    The seeded bill verifies at 805 t with 65 t in review. The demo has the
    engineer approve the two amber drains on camera; that must move exactly
    the review tonnage and leave the hold untouched.
    """
    api_app = verified["api"]
    amber = [
        drain_id
        for drain_id, row in verified["truth"]["drains"].items()
        if row["expectedColour"] == "AMBER"
    ]
    assert sorted(amber) == ["6", "8"]

    try:
        for drain_id in amber:
            status, body = call(
                api_app, "POST /decision",
                body={"billId": "B1", "drainId": drain_id, "decision": "APPROVE",
                      "note": "GPS gap explained by the underpass; work verified on site."},
            )
            assert status == 200

        summary = body["summary"]
        assert summary["verifiedTonnes"] == pytest.approx(870.0, abs=0.05)
        assert summary["reviewTonnes"] == pytest.approx(0.0, abs=0.05)
        assert summary["heldTonnes"] == pytest.approx(370.0, abs=0.05)
        assert summary["heldRupees"] == 666000
        assert summary["claimedTonnes"] == pytest.approx(1240.0, abs=0.05)

        # ...and it survives a reload, which is what the screen does next.
        status, bill = call(api_app, "GET /bill/{billId}", path={"billId": "B1"})
        assert bill["summary"]["verifiedTonnes"] == pytest.approx(870.0, abs=0.05)
        assert bill["summary"]["heldTonnes"] == pytest.approx(370.0, abs=0.05)
    finally:
        from common import store

        for drain_id in amber:
            store.update_fields(
                store.bill_pk("B1"), store.drain_sk(drain_id),
                {"decision": None, "note": None, "decidedAt": None},
            )
        call(api_app, "POST /verify/{billId}", path={"billId": "B1"})


def test_holding_the_hero_drain_leaves_the_money_held(verified):
    api_app = verified["api"]

    try:
        status, body = call(
            api_app, "POST /decision",
            body={"billId": "B1", "drainId": "14", "decision": "HOLD",
                  "note": "Truck never reached the dump site. Referred to vigilance."},
        )

        assert status == 200
        assert body["drain"]["heldTonnes"] == pytest.approx(192.0, abs=0.05)
        assert body["summary"]["heldTonnes"] == pytest.approx(370.0, abs=0.05)
        assert body["note"].startswith("Truck never reached")
    finally:
        from common import store

        store.update_fields(
            store.bill_pk("B1"), store.drain_sk("14"),
            {"decision": None, "note": None, "decidedAt": None},
        )
        call(api_app, "POST /verify/{billId}", path={"billId": "B1"})


def test_the_evidence_summary_is_written_once_and_cached(verified):
    api_app = verified["api"]

    status, first = call(
        api_app, "POST /drain/{drainId}/summary", path={"drainId": "14"}, body={}
    )
    assert status == 200
    assert first["cached"] is False
    assert set(first["basedOnRules"]) == {"R3", "R5", "R8"}

    status, second = call(
        api_app, "POST /drain/{drainId}/summary", path={"drainId": "14"}, body={}
    )
    assert second["cached"] is True
    assert second["summary"] == first["summary"]


def test_drain_14_r8_names_the_times_it_compares(verified):
    """R8 quotes the slip's time-in, the departure and the last fix, and its
    minute counts agree with those clock times - no 40-against-44 drift."""
    import datetime
    import re

    _, drain = call(verified["api"], "GET /drain/{drainId}", path={"drainId": "14"})

    for trip in drain["trips"]:
        r8 = next(item for item in trip["findings"] if item["rule"] == "R8")
        evidence = r8["evidence"]
        match = re.fullmatch(
            r"The slip records time-in at (\d\d:\d\d), (\d+) minutes before the truck left "
            r"the drain at (\d\d:\d\d)\. The GPS trace never reaches the dump site; its "
            r"last fix, at (\d\d:\d\d), is (\d+) minutes after the slip's time-in\.",
            r8["message"],
        )
        assert match, r8["message"]
        time_in, lead, left, last, minutes = match.groups()

        def clock(text):
            hour, minute = map(int, text.split(":"))
            return hour * 60 + minute

        departure = datetime.datetime.fromisoformat(evidence["departure"])
        arrival = datetime.datetime.fromisoformat(evidence["arrival"])
        assert time_in == evidence["timeIn"] == trip["slip"]["timeIn"]
        assert left == departure.strftime("%H:%M")
        assert last == arrival.strftime("%H:%M")
        assert evidence["arrivalKind"] == "lastFix"
        assert int(lead) == clock(left) - clock(time_in)
        assert int(minutes) == clock(last) - clock(time_in)
        assert abs(evidence["minutesEarly"] - int(minutes)) < 1
