"""The Textract slip parser, including the crumpled-slip cases."""

import pytest

from common import textract


# ------------------------------------------------------------ field parsers
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("9.80 T", 9.8),
        ("9.80T", 9.8),
        ("  12.60 t ", 12.6),
        ("22,400 kg", 22.4),
        ("9800kg", 9.8),
        ("9.8O T", 9.8),        # OCR read zero as a letter O
        ("l0.50 T", 10.5),      # ...and one as a lowercase L
        ("22400", 22.4),        # no unit, too big to be tonnes
        ("14", 14.0),
        ("--", None),
        ("", None),
        ("n/a", None),
        ("not a weight", None),
        (None, None),
    ],
)
def test_parse_weight_tonnes(raw, expected):
    assert textract.parse_weight_tonnes(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("09:42", "09:42"),
        ("9:42", "09:42"),
        ("O9:42", "09:42"),
        ("9.42 AM", "09:42"),
        ("2:15 pm", "14:15"),
        ("12:30 AM", "00:30"),
        ("12:30 PM", "12:30"),
        ("0942", "09:42"),
        ("09-42", "09:42"),
        ("25:70", None),
        ("--", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_time(raw, expected):
    assert textract.parse_time(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("MH 01 AB 1234", "MH 01 AB 1234"),
        ("mh 01 ab 1234", "MH 01 AB 1234"),
        ("MH O1 AB l234", "MH 01 AB 1234"),
        ("MH-01-AB-1234", "MH 01 AB 1234"),
        ("--", None),
        (None, None),
    ],
)
def test_parse_vehicle_no(raw, expected):
    assert textract.parse_vehicle_no(raw) == expected


def test_letters_are_not_mangled_by_the_digit_fixer():
    """The O in 'GO' is a letter; only the numeric groups get corrected."""
    assert textract.parse_vehicle_no("MH 02 GO 3456") == "MH 02 GO 3456"


# -------------------------------------------------------------- whole slips
def test_parse_slip_reads_a_clean_slip(fixture_response):
    parsed = textract.parse_slip(fixture_response("textract_slip_good"))
    fields = parsed["fields"]

    assert fields["ticketNo"]["value"] == "WB-2026-04871"
    assert fields["vehicleNo"]["value"] == "MH 01 AB 1234"
    assert fields["gross"]["value"] == 22.4
    assert fields["tare"]["value"] == 12.6
    assert fields["net"]["value"] == 9.8
    assert fields["timeIn"]["value"] == "09:42"
    assert fields["timeOut"]["value"] == "10:07"

    assert parsed["missingFields"] == []
    assert parsed["lowConfidenceFields"] == []
    assert parsed["confidenceAvg"] > 90


def test_parse_slip_survives_a_garbled_slip(fixture_response):
    parsed = textract.parse_slip(fixture_response("textract_slip_garbled"))
    fields = parsed["fields"]

    # The values are still recovered despite the OCR confusions.
    assert fields["vehicleNo"]["value"] == "MH 01 AB 1234"
    assert fields["net"]["value"] == 9.8
    assert fields["gross"]["value"] == 22.4
    assert fields["timeIn"]["value"] == "09:42"

    # The tare was a dash and the time-out had no answer at all.
    assert fields["tare"]["value"] is None
    assert fields["timeOut"]["value"] is None
    assert set(parsed["missingFields"]) == {"tare", "timeOut"}

    # ...and the whole thing is flagged as low confidence for the UI.
    assert "net" in parsed["lowConfidenceFields"]
    assert parsed["confidenceAvg"] < 80


def test_net_weight_is_recovered_from_gross_minus_tare(fixture_response):
    response = fixture_response("textract_slip_good")

    for block in response["Blocks"]:
        if block.get("BlockType") == "QUERY_RESULT" and block["Id"] == "answer-net_weight":
            block["Text"] = "~~~~"

    parsed = textract.parse_slip(response)
    net = parsed["fields"]["net"]

    assert net["value"] == pytest.approx(9.8)
    assert net["derived"] == "gross-tare"
    assert net["lowConfidence"] is True


def test_parse_slip_handles_an_empty_response():
    parsed = textract.parse_slip({})

    assert parsed["confidenceAvg"] == 0.0
    assert len(parsed["missingFields"]) == 8
    assert all(field["value"] is None for field in parsed["fields"].values())


def test_parse_slip_takes_the_most_confident_answer():
    response = {
        "Blocks": [
            {
                "BlockType": "QUERY",
                "Id": "q",
                "Query": {"Alias": "NET_WEIGHT", "Text": "What is the net weight?"},
                "Relationships": [{"Type": "ANSWER", "Ids": ["a1", "a2"]}],
            },
            {"BlockType": "QUERY_RESULT", "Id": "a1", "Text": "1.00 T", "Confidence": 40.0},
            {"BlockType": "QUERY_RESULT", "Id": "a2", "Text": "9.80 T", "Confidence": 95.0},
        ]
    }

    assert textract.parse_slip(response)["fields"]["net"]["value"] == 9.8


def test_mock_mode_returns_a_parseable_response(monkeypatch):
    """MOCK_AWS=1 goes through the same parser as a real response."""
    response = textract.analyze_document(b"not really an image", key="slips/B1/14-001.png")
    parsed = textract.parse_slip(response)

    assert parsed["fields"]["net"]["value"] == 9.8
