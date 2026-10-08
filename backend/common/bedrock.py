"""Amazon Bedrock: the before/after photo check, and the evidence summary.

The photo check uses the Converse API with a single tool whose input schema is
the answer we want, and ``toolChoice`` forcing that tool. That makes the shape
of the answer the model's only option, instead of parsing prose.
"""

import json
import re

from . import awsclients, config, mocks
from .jsonlog import log
from .retry import call_with_retry

TOOL_NAME = "record_photo_check"

LOAD_TYPES = ("silt", "debris", "unclear")

PHOTO_TOOL = {
    "toolSpec": {
        "name": TOOL_NAME,
        "description": (
            "Record the assessment of one desilting evidence photo. "
            "Call this exactly once."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "cleared": {
                        "type": "boolean",
                        "description": (
                            "True if the drain channel in the photo looks cleared: "
                            "the bed or invert is visible rather than filled with silt."
                        ),
                    },
                    "load_type": {
                        "type": "string",
                        "enum": list(LOAD_TYPES),
                        "description": (
                            "silt for drain muck, debris for construction rubble such "
                            "as broken brick or concrete, unclear if no load is visible "
                            "or the photo cannot be judged."
                        ),
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                        "description": "Your confidence in this assessment.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "One sentence of visible evidence for the call.",
                    },
                },
                "required": ["cleared", "load_type", "confidence", "notes"],
            }
        },
    }
}

PHOTO_PROMPT = (
    "You are checking one photograph submitted as proof that a municipal storm-water "
    "drain was desilted.\n\n"
    "Judge only what is visible. Do not guess at anything outside the frame.\n"
    "- cleared: is the drain channel clear, with the bed or invert visible?\n"
    "- load_type: if a truck load or heap is visible, is it drain silt, or is it "
    "construction debris (broken brick, concrete, tiles, rubble)? Use unclear if no "
    "load is visible or the image is too dark, blurred or backlit to judge.\n\n"
    f"Record your assessment by calling {TOOL_NAME}."
)

# Content-type -> the format name Converse expects.
IMAGE_FORMATS = {
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}


def image_format(content_type=None, key=""):
    if content_type and content_type.lower() in IMAGE_FORMATS:
        return IMAGE_FORMATS[content_type.lower()]
    lowered = key.lower()
    for suffix, name in (
        (".png", "png"), (".gif", "gif"), (".webp", "webp"),
        (".jpeg", "jpeg"), (".jpg", "jpeg"),
    ):
        if lowered.endswith(suffix):
            return name
    return "jpeg"


# ------------------------------------------------------------------ the call
# Fixed sampling, so the same photo gets the same verdict on every run.
TEMPERATURE = 0.0
TOP_P = 1.0


def inference_config(model_id, max_tokens):
    """maxTokens, plus fixed sampling on models that accept it.

    Anthropic models get maxTokens only: the Claude 5 family rejects temperature
    and Claude 4.5 rejects temperature and topP together (DECISIONS.md).
    """
    settings = {"maxTokens": max_tokens}
    if "anthropic." not in model_id:
        settings["temperature"] = TEMPERATURE
        settings["topP"] = TOP_P
    return settings


def check_photo(image_bytes, *, key="", content_type=None, model_id=None):
    """Return the raw Converse response for one photo (real or mocked)."""
    if config.mock_aws():
        log("bedrock_mocked", key=key, call="converse_photo")
        return mocks.bedrock_photo_check(key)

    client = awsclients.client("bedrock-runtime")
    model = model_id or config.vision_model_id()

    return call_with_retry(
        lambda: client.converse(
            modelId=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"text": PHOTO_PROMPT},
                        {
                            "image": {
                                "format": image_format(content_type, key),
                                "source": {"bytes": image_bytes},
                            }
                        },
                    ],
                }
            ],
            toolConfig={
                "tools": [PHOTO_TOOL],
                "toolChoice": {"tool": {"name": TOOL_NAME}},
            },
            inferenceConfig=inference_config(model, 512),
        ),
        what="bedrock.converse.photo",
    )


def summarise(prompt, *, model_id=None, max_tokens=300):
    """Plain text Converse call, for the Day 2 evidence summary."""
    if config.mock_aws():
        log("bedrock_mocked", call="converse_text")
        return mocks.bedrock_text(prompt)

    client = awsclients.client("bedrock-runtime")
    model = model_id or config.text_model_id()

    return call_with_retry(
        lambda: client.converse(
            modelId=model,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig=inference_config(model, max_tokens),
        ),
        what="bedrock.converse.text",
    )


# ----------------------------------------------------------------- the parse
def _content_blocks(response):
    return ((response.get("output") or {}).get("message") or {}).get("content") or []


def response_text(response):
    """Concatenate the text blocks of a Converse response."""
    parts = [block.get("text", "") for block in _content_blocks(response) if "text" in block]
    return "".join(parts).strip()


def _tool_input(response):
    for block in _content_blocks(response):
        use = block.get("toolUse")
        if use and use.get("name") == TOOL_NAME:
            payload = use.get("input")
            if isinstance(payload, dict):
                return payload
    return None


def _json_from_text(response):
    """Fallback: the model answered in prose with JSON somewhere inside."""
    text = response_text(response)
    if not text:
        return None

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None

    if candidate is None:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        candidate = brace.group(0) if brace else None

    if candidate is None:
        return None

    try:
        payload = json.loads(candidate)
    except ValueError:
        return None

    return payload if isinstance(payload, dict) else None


def _coerce_bool(value):
    if isinstance(value, bool):
        return value, True
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "y", "1"):
            return True, True
        if lowered in ("false", "no", "n", "0"):
            return False, True
    if isinstance(value, (int, float)):
        return bool(value), True
    return False, False


def _coerce_confidence(value):
    if isinstance(value, bool):
        return 0.0, False
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 1.0:
            # A model that answered 85 meant 85%.
            number = number / 100.0 if number <= 100.0 else 1.0
        return round(min(max(number, 0.0), 1.0), 3), True
    if isinstance(value, str):
        words = {"high": 0.85, "medium": 0.6, "moderate": 0.6, "low": 0.3}
        lowered = value.strip().lower()
        if lowered in words:
            return words[lowered], False
        try:
            return _coerce_confidence(float(lowered))
        except ValueError:
            return 0.0, False
    return 0.0, False


def parse_photo_check(response):
    """Raw Converse response -> the photo verdict, with the schema enforced.

    Never raises: a model that ignores the tool, invents a load type or answers
    with the wrong types degrades to load_type 'unclear' with ok=False, which the
    rules treat as a soft flag (R4).
    """
    payload = _tool_input(response)
    source = "tool_use"

    if payload is None:
        payload = _json_from_text(response)
        source = "text_json"

    if payload is None:
        return {
            "cleared": False,
            "load_type": "unclear",
            "confidence": 0.0,
            "notes": response_text(response)[:500] or "Model returned no usable answer.",
            "ok": False,
            "source": "none",
            "problems": ["no_structured_output"],
            "stopReason": response.get("stopReason"),
            "usage": response.get("usage"),
        }

    problems = []

    cleared, cleared_ok = _coerce_bool(payload.get("cleared"))
    if not cleared_ok:
        problems.append("cleared_not_boolean")

    load_type = payload.get("load_type")
    if not isinstance(load_type, str) or load_type.strip().lower() not in LOAD_TYPES:
        problems.append("load_type_out_of_enum")
        load_type = "unclear"
    else:
        load_type = load_type.strip().lower()

    confidence, confidence_ok = _coerce_confidence(payload.get("confidence"))
    if not confidence_ok:
        problems.append("confidence_not_a_number")

    notes = payload.get("notes")
    if not isinstance(notes, str):
        problems.append("notes_not_a_string")
        notes = "" if notes is None else json.dumps(notes, default=str)

    return {
        "cleared": cleared,
        "load_type": load_type,
        "confidence": confidence,
        "notes": notes.strip()[:500],
        "ok": not problems,
        "source": source,
        "problems": problems,
        "stopReason": response.get("stopReason"),
        "usage": response.get("usage"),
    }
