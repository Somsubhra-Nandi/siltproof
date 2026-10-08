"""Regression tests over raw responses captured from real AWS.

The files in tests/fixtures/live/ are what scripts/live_smoke.py saved from
ap-south-1 (ResponseMetadata stripped). If a parser change breaks on them, it
breaks on the real service too. A fixture that has not been captured yet is
skipped, not failed.
"""

import json
import pathlib

import pytest

from common import bedrock, location, store, textract

LIVE = pathlib.Path(__file__).parent / "fixtures" / "live"


def live(name):
    path = LIVE / f"{name}.json"
    if not path.exists():
        pytest.skip(f"{path.name} not captured yet; run scripts/live_smoke.py --live")
    return json.loads(path.read_text())


# ---------------------------------------------------------------- textract
def test_live_textract_slip_parses_every_field():
    parsed = textract.parse_slip(live("textract_analyze_document"))
    values = {name: field["value"] for name, field in parsed["fields"].items()}

    # What data/gen_slips.py printed on slips/B1/9-001.png.
    assert values == {
        "ticketNo": "WB-2026-76478",
        "vehicleNo": "MH 05 ST 2468",
        "gross": 22.9,
        "tare": 12.6,
        "net": 10.3,
        "timeIn": "07:28",
        "timeOut": "07:44",
        "site": "Ward storm water drain desilting",
    }
    assert parsed["missingFields"] == []
    assert parsed["modelVersion"]


def test_live_textract_answers_hang_off_query_relationships():
    response = live("textract_analyze_document")
    by_id = {block["Id"]: block for block in response["Blocks"]}
    queries = [block for block in response["Blocks"] if block["BlockType"] == "QUERY"]

    assert {q["Query"]["Alias"] for q in queries} == {alias for alias, _ in textract.QUERIES}
    for query in queries:
        answer_ids = [
            answer_id
            for relationship in query["Relationships"]
            if relationship["Type"] == "ANSWER"
            for answer_id in relationship["Ids"]
        ]
        assert [by_id[i]["BlockType"] for i in answer_ids] == ["QUERY_RESULT"]


# ----------------------------------------------------------------- bedrock
# Haiku 4.5 from the smoke run, then Nova Pro and Lite (apac.* profiles) on two
# generated photos, captured when Haiku was blocked by a Marketplace payment error.
PHOTO_FIXTURES = [
    "bedrock_converse_photo",
    "bedrock_converse_photo_nova_pro_drain9",
    "bedrock_converse_photo_nova_pro_drain17",
    "bedrock_converse_photo_nova_lite_drain9",
    "bedrock_converse_photo_nova_lite_drain17",
]


@pytest.mark.parametrize("name", PHOTO_FIXTURES)
def test_live_bedrock_photo_check_comes_back_as_a_tool_use(name):
    parsed = bedrock.parse_photo_check(live(name))

    assert parsed["source"] == "tool_use"
    assert parsed["ok"], parsed["problems"]
    assert parsed["load_type"] in bedrock.LOAD_TYPES
    assert 0.0 <= parsed["confidence"] <= 1.0



def test_live_bedrock_summary_is_plain_text():
    # Nova Pro at temperature 0, given the API's SUMMARY_PROMPT for two findings.
    response = live("bedrock_converse_summary")
    text = bedrock.response_text(response)

    assert response["stopReason"] == "end_turn"
    assert not any("toolUse" in block for block in response["output"]["message"]["content"])
    assert "9-003" in text and "9-001" in text
    assert text.count(". ") + 1 == 2, text   # the prompt asks for exactly two sentences


# ---------------------------------------------------------------- location
def test_live_route_geometry_and_summary_are_parsed():
    response = live("location_calculate_routes")
    parsed = location.parse_route(response)

    assert parsed["ok"]
    # One vehicle leg; its LineString is the whole route.
    assert len(parsed["points"]) == len(response["Routes"][0]["Legs"][0]["Geometry"]["LineString"])
    assert parsed["distanceM"] == response["Routes"][0]["Summary"]["Distance"]
    assert parsed["durationS"] == response["Routes"][0]["Summary"]["Duration"]
    for lon, lat in parsed["points"]:
        assert 72.7 < lon < 73.1 and 18.9 < lat < 19.3   # Mumbai, [lon, lat] order


def test_live_route_survives_the_dynamodb_round_trip():
    parsed = location.parse_route(live("location_calculate_routes"))
    back = store.from_dynamo(store.to_dynamo(parsed))
    assert back["points"] == parsed["points"]
