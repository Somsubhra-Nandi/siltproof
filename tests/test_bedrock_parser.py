"""The Bedrock photo-check parser, including every way a model can misbehave."""

import pytest

from common import bedrock


def test_forced_tool_output_is_read_straight_through(fixture_response):
    verdict = bedrock.parse_photo_check(fixture_response("bedrock_photo_silt"))

    assert verdict["cleared"] is True
    assert verdict["load_type"] == "silt"
    assert verdict["confidence"] == 0.93
    assert verdict["ok"] is True
    assert verdict["source"] == "tool_use"
    assert verdict["problems"] == []
    assert "silt" in verdict["notes"]


def test_debris_is_the_case_that_holds_a_payment(fixture_response):
    verdict = bedrock.parse_photo_check(fixture_response("bedrock_photo_debris"))

    assert verdict["load_type"] == "debris"
    assert verdict["ok"] is True


def test_unclear_is_a_soft_flag_not_a_failure(fixture_response):
    verdict = bedrock.parse_photo_check(fixture_response("bedrock_photo_unclear"))

    assert verdict["load_type"] == "unclear"
    assert verdict["cleared"] is False
    assert verdict["ok"] is True
    assert verdict["confidence"] < 0.5


def test_json_in_prose_is_recovered_when_the_tool_is_ignored(fixture_response):
    verdict = bedrock.parse_photo_check(fixture_response("bedrock_photo_textonly"))

    assert verdict["source"] == "text_json"
    assert verdict["load_type"] == "silt"
    assert verdict["cleared"] is True
    assert verdict["ok"] is True


def test_wrong_types_degrade_instead_of_raising(fixture_response):
    verdict = bedrock.parse_photo_check(fixture_response("bedrock_photo_malformed"))

    # cleared "yes" is understood, but the rest is not usable.
    assert verdict["cleared"] is True
    assert verdict["load_type"] == "unclear"      # "rubble" is outside the enum
    assert verdict["confidence"] == 0.85          # "high"
    assert verdict["ok"] is False
    assert "load_type_out_of_enum" in verdict["problems"]
    assert "confidence_not_a_number" in verdict["problems"]
    assert "notes_not_a_string" in verdict["problems"]


def test_a_response_with_no_usable_answer():
    verdict = bedrock.parse_photo_check(
        {"output": {"message": {"content": [{"text": "I cannot help with that."}]}}}
    )

    assert verdict["ok"] is False
    assert verdict["load_type"] == "unclear"
    assert verdict["problems"] == ["no_structured_output"]
    assert "cannot help" in verdict["notes"]


def test_an_empty_response_does_not_raise():
    verdict = bedrock.parse_photo_check({})

    assert verdict["ok"] is False
    assert verdict["load_type"] == "unclear"
    assert verdict["confidence"] == 0.0


@pytest.mark.parametrize(
    "value,expected",
    [(0.9, 0.9), (1, 1.0), (85, 0.85), (150, 1.0), (-2, 0.0), ("0.4", 0.4), ("high", 0.85)],
)
def test_confidence_is_coerced_into_zero_to_one(fixture_response, value, expected):
    response = fixture_response("bedrock_photo_silt")
    response["output"]["message"]["content"][0]["toolUse"]["input"]["confidence"] = value

    assert bedrock.parse_photo_check(response)["confidence"] == pytest.approx(expected)


def test_notes_are_truncated():
    response = {
        "output": {"message": {"content": [
            {"toolUse": {"name": bedrock.TOOL_NAME, "input": {
                "cleared": True, "load_type": "silt", "confidence": 0.8, "notes": "x" * 2000,
            }}}
        ]}}
    }

    assert len(bedrock.parse_photo_check(response)["notes"]) == 500


def test_the_tool_schema_pins_the_four_fields_we_rely_on():
    schema = bedrock.PHOTO_TOOL["toolSpec"]["inputSchema"]["json"]

    assert set(schema["required"]) == {"cleared", "load_type", "confidence", "notes"}
    assert schema["properties"]["load_type"]["enum"] == ["silt", "debris", "unclear"]


@pytest.mark.parametrize(
    "content_type,key,expected",
    [
        ("image/jpeg", "", "jpeg"),
        ("image/png", "", "png"),
        (None, "photos/B1/drain1/after-01.JPG", "jpeg"),
        (None, "photos/B1/drain1/after-01.png", "png"),
        (None, "photos/B1/drain1/after-01.heic", "jpeg"),  # fall back, do not crash
    ],
)
def test_image_format_detection(content_type, key, expected):
    assert bedrock.image_format(content_type, key) == expected


def test_mock_mode_uses_the_manifest(monkeypatch, tmp_path):
    """A mocked call returns what the generator said that photo shows."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"photos": {"photos/B1/drain3/load-01.jpg": '
        '{"loadType": "debris", "cleared": true, "confidence": 0.77}}}'
    )
    monkeypatch.setenv("MOCK_MANIFEST_PATH", str(manifest))

    response = bedrock.check_photo(b"bytes", key="photos/B1/drain3/load-01.jpg")
    verdict = bedrock.parse_photo_check(response)

    assert verdict["load_type"] == "debris"
    assert verdict["confidence"] == 0.77


def test_mock_mode_falls_back_to_key_hints():
    verdict = bedrock.parse_photo_check(
        bedrock.check_photo(b"bytes", key="photos/B1/drain3/load-debris-01.jpg")
    )

    assert verdict["load_type"] == "debris"


def test_summarise_returns_text(fixture_response):
    text = bedrock.response_text(bedrock.summarise("why was this held?"))

    assert "dump site" in text


class RecordingClient:
    """Stands in for bedrock-runtime and keeps the kwargs of each converse call."""

    def __init__(self):
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {"output": {"message": {"content": [{"text": "ok"}]}}}


@pytest.fixture
def recording_client(monkeypatch):
    monkeypatch.delenv("MOCK_AWS", raising=False)
    recorder = RecordingClient()
    monkeypatch.setattr(bedrock.awsclients, "client", lambda service: recorder)
    return recorder


def test_nova_gets_fixed_sampling_on_both_calls(recording_client):
    model = "apac.amazon.nova-pro-v1:0"
    bedrock.check_photo(b"bytes", key="a.jpg", model_id=model)
    bedrock.summarise("why?", model_id=model, max_tokens=300)

    photo, summary = (call["inferenceConfig"] for call in recording_client.calls)
    assert photo == {"maxTokens": 512, "temperature": 0.0, "topP": 1.0}
    assert summary == {"maxTokens": 300, "temperature": 0.0, "topP": 1.0}


def test_anthropic_models_get_max_tokens_only(recording_client):
    # Claude 5 rejects temperature; Claude 4.5 rejects temperature with topP.
    for model in ("in.anthropic.claude-haiku-4-5-20251001-v1:0", "global.anthropic.claude-sonnet-5"):
        bedrock.check_photo(b"bytes", key="a.jpg", model_id=model)

    assert [call["inferenceConfig"] for call in recording_client.calls] == [{"maxTokens": 512}] * 2
