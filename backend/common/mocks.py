"""Canned, API-shaped responses for MOCK_AWS=1.

These return the same response *shape* the real services return, so the real
parsers in textract.py / bedrock.py / location.py run unchanged. Nothing here
is ever used when MOCK_AWS is unset.

Two layers:

1. Static fixtures in ``fixtures/``, chosen from hints in the S3 key.
2. An optional generator manifest (``MOCK_MANIFEST_PATH``) that overwrites the
   fixture's values with what was actually printed on that slip, or with the
   load type that photo was drawn to show. This is what makes the offline
   end-to-end test meaningful instead of circular.
"""

import copy
import functools
import json
import pathlib

from . import config

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures"


@functools.lru_cache(maxsize=None)
def load_fixture(name):
    with open(FIXTURE_DIR / f"{name}.json", encoding="utf-8") as handle:
        return json.load(handle)


def _manifest():
    path = config.mock_manifest_path()
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _entry(section, key):
    return (_manifest().get(section) or {}).get(key) or {}


# ------------------------------------------------------------------ textract
# Manifest field -> the query alias whose answer it replaces.
SLIP_FIELD_ALIASES = {
    "ticketNo": "TICKET_NO",
    "vehicleNo": "VEHICLE_NO",
    "gross": "GROSS_WEIGHT",
    "tare": "TARE_WEIGHT",
    "net": "NET_WEIGHT",
    "timeIn": "TIME_IN",
    "timeOut": "TIME_OUT",
    "site": "SITE",
}


def _set_answer(response, alias, text):
    """Rewrite the QUERY_RESULT text for one alias, in place."""
    blocks = response["Blocks"]
    by_id = {block["Id"]: block for block in blocks}

    for block in blocks:
        if block.get("BlockType") != "QUERY":
            continue
        if block.get("Query", {}).get("Alias") != alias:
            continue
        for relationship in block.get("Relationships", []):
            if relationship.get("Type") != "ANSWER":
                continue
            for answer_id in relationship.get("Ids", []):
                answer = by_id.get(answer_id)
                if answer is not None:
                    answer["Text"] = text


def textract_analyze_document(key):
    """A mocked AnalyzeDocument response for the slip at this S3 key."""
    name = "textract_slip_garbled" if "garbled" in key else "textract_slip_good"
    response = copy.deepcopy(load_fixture(name))

    values = _entry("slips", key)
    for field, alias in SLIP_FIELD_ALIASES.items():
        if field in values and values[field] is not None:
            _set_answer(response, alias, str(values[field]))

    return response


# ------------------------------------------------------------------- bedrock
LOAD_TYPE_FIXTURES = {
    "silt": "bedrock_photo_silt",
    "debris": "bedrock_photo_debris",
    "unclear": "bedrock_photo_unclear",
}


def bedrock_photo_check(key):
    """A mocked Converse response for the photo at this S3 key."""
    values = _entry("photos", key)
    load_type = values.get("loadType")

    if load_type is None:
        # Fall back to hints in the key so the mock still behaves sensibly
        # without a manifest.
        lowered = key.lower()
        if "debris" in lowered or "rubble" in lowered:
            load_type = "debris"
        elif "unclear" in lowered or "backlit" in lowered:
            load_type = "unclear"
        else:
            load_type = "silt"

    response = copy.deepcopy(load_fixture(LOAD_TYPE_FIXTURES.get(load_type, "bedrock_photo_silt")))
    payload = response["output"]["message"]["content"][0]["toolUse"]["input"]

    if "cleared" in values:
        payload["cleared"] = bool(values["cleared"])
    if "confidence" in values:
        payload["confidence"] = float(values["confidence"])
    if values.get("notes"):
        payload["notes"] = str(values["notes"])

    return response


def bedrock_text(_prompt):
    """A mocked Converse response for the Day 2 evidence summary."""
    return copy.deepcopy(load_fixture("bedrock_summary_text"))


# ------------------------------------------------------------------ location
def location_calculate_routes(origin, destination):
    """A mocked CalculateRoutes response, re-anchored onto the real endpoints.

    The fixture's geometry is a straight-ish line; shifting it onto the
    requested origin and destination keeps the mock usable for any ward.
    """
    response = copy.deepcopy(load_fixture("location_calculate_routes"))
    line = response["Routes"][0]["Legs"][0]["Geometry"]["LineString"]
    steps = len(line) - 1

    moved = []
    for index in range(len(line)):
        fraction = index / steps
        moved.append(
            [
                origin[0] + (destination[0] - origin[0]) * fraction,
                origin[1] + (destination[1] - origin[1]) * fraction,
            ]
        )

    response["Routes"][0]["Legs"][0]["Geometry"]["LineString"] = moved
    return response
