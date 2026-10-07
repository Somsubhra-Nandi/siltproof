"""Amazon Textract: read a weighbridge slip with AnalyzeDocument QUERIES.

One call, eight queries. The parser is deliberately forgiving: a crumpled,
badly-lit slip loses fields and the OCR confuses O/0 and l/1, and the plan's
answer to that is to keep the extracted facts with their confidence and let the
rules decide (plan section 9).
"""

import re

from . import awsclients, config, mocks
from .jsonlog import log
from .retry import call_with_retry

# Alias -> question. Aliases become the keys of the parsed result.
QUERIES = [
    ("TICKET_NO", "What is the ticket number?"),
    ("VEHICLE_NO", "What is the vehicle number?"),
    ("GROSS_WEIGHT", "What is the gross weight?"),
    ("TARE_WEIGHT", "What is the tare weight?"),
    ("NET_WEIGHT", "What is the net weight?"),
    ("TIME_IN", "What is the time in?"),
    ("TIME_OUT", "What is the time out?"),
    ("SITE", "What is the site?"),
]

# Below this, a field is kept but marked low-confidence for the UI.
LOW_CONFIDENCE = 80.0

# Characters OCR confuses inside otherwise numeric fields. Only ever applied
# to text that already contains at least one real digit, so a word like
# "none" is not quietly read as a number.
OCR_DIGITS = str.maketrans(
    {"O": "0", "o": "0", "l": "1", "I": "1", "L": "1", "|": "1", "S": "5"}
)


def has_digit(text):
    return any(character.isdigit() for character in str(text))

_NO_ANSWER = {"", "-", "--", "---", "n/a", "na", "none", "nil", "?"}


# ------------------------------------------------------------------ the call
def analyze_document(image_bytes, key=""):
    """Return the raw AnalyzeDocument response (real or mocked)."""
    if config.mock_aws():
        log("textract_mocked", key=key)
        return mocks.textract_analyze_document(key)

    client = awsclients.client("textract")
    return call_with_retry(
        lambda: client.analyze_document(
            Document={"Bytes": image_bytes},
            FeatureTypes=["QUERIES"],
            QueriesConfig={
                "Queries": [{"Text": text, "Alias": alias} for alias, text in QUERIES]
            },
        ),
        what="textract.analyze_document",
    )


# ----------------------------------------------------------------- the parse
def _answers(response):
    """alias -> (text, confidence), taking the best answer per query."""
    blocks = response.get("Blocks", []) or []
    by_id = {block.get("Id"): block for block in blocks}
    found = {}

    for block in blocks:
        if block.get("BlockType") != "QUERY":
            continue
        alias = (block.get("Query") or {}).get("Alias")
        if not alias:
            continue

        best = None
        for relationship in block.get("Relationships", []) or []:
            if relationship.get("Type") != "ANSWER":
                continue
            for answer_id in relationship.get("Ids", []) or []:
                answer = by_id.get(answer_id) or {}
                text = (answer.get("Text") or "").strip()
                confidence = float(answer.get("Confidence") or 0.0)
                if not text:
                    continue
                if best is None or confidence > best[1]:
                    best = (text, confidence)

        found[alias] = best

    return found


def parse_weight_tonnes(raw):
    """'9.80 T' / '9,800 kg' / '9.8O T' -> 9.8. None if unreadable."""
    if raw is None:
        return None

    text = str(raw).strip().lower()
    if text in _NO_ANSWER:
        return None

    if not has_digit(text):
        # Prose, not a weight. Without this, "not a weight" becomes 0.0 once
        # the OCR fixer turns its letter o into a zero.
        return None

    is_kg = "kg" in text or "kgs" in text
    # Drop units and separators before fixing OCR digit confusions.
    cleaned = re.sub(r"(kgs|kg|mt|tonnes|tonne|ton|tons|t)\b", "", text)
    cleaned = cleaned.replace(",", "").replace(" ", "")
    cleaned = cleaned.translate(OCR_DIGITS)

    match = re.search(r"\d+(?:\.\d+)?", cleaned)
    if match is None:
        return None

    value = float(match.group(0))
    if value == 0:
        return None

    # A slip never shows a net weight of 9,800 tonnes, so a big number in a
    # field with no unit is kilograms.
    if is_kg or value > 200:
        value = value / 1000.0

    return round(value, 3)


def parse_time(raw):
    """'09:42' / 'O9:42' / '9.42 AM' / '0942' -> '09:42'. None if unreadable."""
    if raw is None:
        return None

    text = str(raw).strip().lower()
    if text in _NO_ANSWER:
        return None

    if not has_digit(text):
        return None

    meridiem = None
    if "pm" in text:
        meridiem = "pm"
    elif "am" in text:
        meridiem = "am"

    cleaned = re.sub(r"[ap]\.?m\.?", "", text).strip()
    cleaned = cleaned.translate(OCR_DIGITS)

    match = re.search(r"(\d{1,2})\s*[:.\-h]\s*(\d{2})", cleaned)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
    else:
        digits = re.sub(r"\D", "", cleaned)
        if len(digits) not in (3, 4):
            return None
        hour, minute = int(digits[:-2]), int(digits[-2:])

    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    return f"{hour:02d}:{minute:02d}"


def parse_vehicle_no(raw):
    """'MH O1 AB l234' -> 'MH 01 AB 1234'. Returns None if unreadable."""
    if raw is None:
        return None

    text = str(raw).strip().upper()
    if text.lower() in _NO_ANSWER:
        return None
    if not has_digit(text):
        return None

    text = re.sub(r"[^A-Z0-9 ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    # Indian plates are LL DD LL DDDD: fix OCR confusions only in the numeric
    # groups, so 'MH O1 AB l234' comes back right without mangling the letters.
    parts = text.split(" ")
    fixed = []
    for index, part in enumerate(parts):
        digits_expected = index in (1, 3)
        if digits_expected:
            part = part.translate(OCR_DIGITS)
        fixed.append(part)

    result = " ".join(fixed)
    return result or None


def _field(alias, answers, value):
    answer = answers.get(alias)
    raw = answer[0] if answer else None
    confidence = round(answer[1], 2) if answer else 0.0
    return {
        "value": value,
        "raw": raw,
        "confidence": confidence,
        "ok": value is not None,
        "lowConfidence": bool(answer) and confidence < LOW_CONFIDENCE,
    }


def parse_slip(response):
    """Raw AnalyzeDocument response -> typed fields with per-field confidence."""
    answers = _answers(response)

    def raw_of(alias):
        answer = answers.get(alias)
        return answer[0] if answer else None

    fields = {
        "ticketNo": _field(
            "TICKET_NO", answers, _clean_text(raw_of("TICKET_NO"))
        ),
        "vehicleNo": _field(
            "VEHICLE_NO", answers, parse_vehicle_no(raw_of("VEHICLE_NO"))
        ),
        "gross": _field(
            "GROSS_WEIGHT", answers, parse_weight_tonnes(raw_of("GROSS_WEIGHT"))
        ),
        "tare": _field(
            "TARE_WEIGHT", answers, parse_weight_tonnes(raw_of("TARE_WEIGHT"))
        ),
        "net": _field("NET_WEIGHT", answers, parse_weight_tonnes(raw_of("NET_WEIGHT"))),
        "timeIn": _field("TIME_IN", answers, parse_time(raw_of("TIME_IN"))),
        "timeOut": _field("TIME_OUT", answers, parse_time(raw_of("TIME_OUT"))),
        "site": _field("SITE", answers, _clean_text(raw_of("SITE"))),
    }

    # A missing net weight can often be recovered from gross - tare.
    if fields["net"]["value"] is None:
        gross, tare = fields["gross"]["value"], fields["tare"]["value"]
        if gross is not None and tare is not None and gross > tare:
            fields["net"] = {
                "value": round(gross - tare, 3),
                "raw": None,
                "confidence": round(
                    min(fields["gross"]["confidence"], fields["tare"]["confidence"]), 2
                ),
                "ok": True,
                "lowConfidence": True,
                "derived": "gross-tare",
            }

    confidences = [f["confidence"] for f in fields.values() if f["ok"]]

    return {
        "fields": fields,
        "missingFields": sorted(name for name, f in fields.items() if not f["ok"]),
        "lowConfidenceFields": sorted(
            name for name, f in fields.items() if f.get("lowConfidence")
        ),
        "confidenceAvg": round(sum(confidences) / len(confidences), 2) if confidences else 0.0,
        "modelVersion": response.get("AnalyzeDocumentModelVersion"),
    }


def _clean_text(raw):
    if raw is None:
        return None
    text = re.sub(r"\s+", " ", str(raw)).strip()
    if text.lower() in _NO_ANSWER:
        return None
    return text or None
