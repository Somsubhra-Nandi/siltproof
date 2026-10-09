"""Input validation for trials: details, upload requests, file signatures and
GPS traces. Pure functions, no AWS, so every branch is unit-tested.

Each validator returns ``(value, errors)`` where errors is a {field: message}
map. Nothing here trusts the browser: the frontend runs the same checks only
to give earlier feedback.
"""

import datetime
import json
import math
import re

from common import geo

from . import limits

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

DRAIN_SOURCES = ("manual_point", "map_selected", "field_approximate", "user_geometry")


# ---------------------------------------------------------------- helpers
def clean_text(value, max_len):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("must be text")
    text = _CONTROL.sub(" ", value).strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_len:
        raise ValueError(f"must be {max_len} characters or fewer")
    return text or None


def number(value, *, low=None, high=None, low_inclusive=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("must be a number")
    value = float(value)
    if math.isnan(value) or math.isinf(value):
        raise ValueError("must be a finite number")
    if low is not None and (value < low if low_inclusive else value <= low):
        raise ValueError(f"must be {'at least' if low_inclusive else 'more than'} {low:g}")
    if high is not None and value > high:
        raise ValueError(f"must be at most {high:g}")
    return value


def lonlat(value):
    """[lon, lat] with range checks. Rejects [0, 0], the classic null fix."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("must be [longitude, latitude]")
    lon = number(value[0], low=-180, high=180)
    lat = number(value[1], low=-90, high=90)
    if lon == 0 and lat == 0:
        raise ValueError("[0, 0] is not a real location")
    return [round(lon, 7), round(lat, 7)]


def line(value, *, min_points, max_points, closed=False):
    if not isinstance(value, list):
        raise ValueError("must be a list of [longitude, latitude] points")
    if not min_points <= len(value) <= max_points:
        raise ValueError(f"must have between {min_points} and {max_points} points")
    points = []
    for index, point in enumerate(value):
        try:
            points.append(lonlat(point))
        except ValueError as exc:
            raise ValueError(f"point {index + 1} {exc}") from exc
    if closed and points[0] != points[-1]:
        points.append(points[0])
    return points


def iso_time(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("must be an ISO 8601 date-time")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("must be an ISO 8601 date-time") from exc


# ---------------------------------------------------------------- details
def _drain_location(raw):
    if not isinstance(raw, dict):
        raise ValueError("must be an object")
    errors = {}
    out = {}

    if raw.get("point") is None and raw.get("line") is None:
        errors["point"] = "give a point or a line"
    if raw.get("point") is not None:
        try:
            out["point"] = lonlat(raw["point"])
        except ValueError as exc:
            errors["point"] = str(exc)
    if raw.get("line") is not None:
        try:
            out["line"] = line(raw["line"], min_points=2, max_points=200)
        except ValueError as exc:
            errors["line"] = str(exc)

    try:
        out["toleranceM"] = number(raw.get("toleranceM", 30), low=5, high=500)
    except ValueError as exc:
        errors["toleranceM"] = str(exc)

    source = raw.get("source", "manual_point")
    if source not in DRAIN_SOURCES:
        errors["source"] = (
            f"must be one of {', '.join(DRAIN_SOURCES)}; a trial location is never "
            "an official or surveyed drain map"
        )
    out["source"] = source

    derived = raw.get("derivedFromPhotos", False)
    if not isinstance(derived, bool):
        errors["derivedFromPhotos"] = "must be true or false"
    out["derivedFromPhotos"] = bool(derived)

    try:
        out["name"] = clean_text(raw.get("name"), 80)
    except ValueError as exc:
        errors["name"] = str(exc)

    for name in ("lengthM", "widthM", "depthM"):
        if raw.get(name) is None:
            continue
        try:
            high = {"lengthM": 20000, "widthM": 200, "depthM": 50}[name]
            out[name] = number(raw[name], low=0, high=high, low_inclusive=False)
        except ValueError as exc:
            errors[name] = str(exc)

    if "point" not in out and "line" in out:
        # A representative point for the map pin: the line's middle vertex.
        out["point"] = out["line"][len(out["line"]) // 2]

    return out, errors


def _disposal_site(raw):
    if not isinstance(raw, dict):
        raise ValueError("must be an object")
    errors = {}
    out = {}
    try:
        out["point"] = lonlat(raw.get("point"))
    except ValueError as exc:
        errors["point"] = str(exc)
    try:
        out["radiusM"] = number(raw.get("radiusM", 150), low=20, high=2000)
    except ValueError as exc:
        errors["radiusM"] = str(exc)
    if raw.get("polygon") is not None:
        try:
            out["polygon"] = line(raw["polygon"], min_points=3, max_points=200, closed=True)
            if len(out["polygon"]) < 4:
                errors["polygon"] = "needs at least three distinct corners"
        except ValueError as exc:
            errors["polygon"] = str(exc)
    try:
        out["name"] = clean_text(raw.get("name"), 80) or "Designated disposal site"
    except ValueError as exc:
        errors["name"] = str(exc)
    return out, errors


def _claim(raw):
    if not isinstance(raw, dict):
        raise ValueError("must be an object")
    errors = {}
    out = {}
    bounds = {
        "quantityTonnes": dict(low=0, high=100_000, low_inclusive=False),
        "ratePerTonne": dict(low=0, high=1_000_000, low_inclusive=False),
        "amountRupees": dict(low=0, high=1e10),
    }
    for name, kwargs in bounds.items():
        if raw.get(name) is None:
            continue
        try:
            out[name] = round(number(raw[name], **kwargs), 3)
        except ValueError as exc:
            errors[name] = str(exc)
    for name, max_len in (("contractor", 120), ("reference", 80)):
        try:
            out[name] = clean_text(raw.get(name), max_len)
        except ValueError as exc:
            errors[name] = str(exc)
    if not any(out.get(name) is not None for name in bounds):
        errors["quantityTonnes"] = "give at least one of quantity, rate or amount"
    return out, errors


def _work_window(raw):
    if not isinstance(raw, dict):
        raise ValueError("must be an object")
    errors = {}
    out = {}
    stamps = {}
    for name in ("start", "end"):
        try:
            stamps[name] = iso_time(raw.get(name))
            out[name] = stamps[name].isoformat()
        except ValueError as exc:
            errors[name] = str(exc)
    if not errors:
        start, end = stamps["start"], stamps["end"]
        if (start.tzinfo is None) != (end.tzinfo is None):
            errors["end"] = "give both times with a timezone offset, or neither"
        elif end <= start:
            errors["end"] = "must be after the start"
        elif (end - start).days > 366:
            errors["end"] = "the window can be at most a year"
    return out, errors


def _truck(raw):
    if not isinstance(raw, dict):
        raise ValueError("must be an object")
    errors = {}
    out = {}
    try:
        vehicle = clean_text(raw.get("vehicleNo"), 20)
        out["vehicleNo"] = normalise_vehicle(vehicle) if vehicle else None
    except ValueError as exc:
        errors["vehicleNo"] = str(exc)
    if raw.get("capacityTonnes") is not None:
        try:
            out["capacityTonnes"] = number(raw["capacityTonnes"], low=0, high=100,
                                           low_inclusive=False)
        except ValueError as exc:
            errors["capacityTonnes"] = str(exc)
    if not out.get("vehicleNo") and out.get("capacityTonnes") is None:
        errors["vehicleNo"] = "give a vehicle number, a capacity, or both"
    return out, errors


DETAIL_VALIDATORS = {
    "drainLocation": _drain_location,
    "disposalSite": _disposal_site,
    "claim": _claim,
    "workWindow": _work_window,
    "truck": _truck,
}


def details(body):
    """Validate a PUT /details body. Returns (updates, clears, errors)."""
    updates, clears, errors = {}, [], {}
    unknown = sorted(set(body) - set(DETAIL_VALIDATORS))
    for name in unknown:
        errors[name] = "is not a trial detail"

    for name, validator in DETAIL_VALIDATORS.items():
        if name not in body:
            continue
        if body[name] is None:
            clears.append(name)
            continue
        try:
            value, field_errors = validator(body[name])
        except ValueError as exc:
            errors[name] = str(exc)
            continue
        if field_errors:
            for field, message in field_errors.items():
                errors[f"{name}.{field}"] = message
        else:
            updates[name] = value

    if not updates and not clears and not errors:
        errors["body"] = "nothing to update"
    return updates, clears, errors


def normalise_vehicle(text):
    """Upper-case, single-spaced, punctuation stripped. 'wb-25 ab 1234' -> 'WB 25 AB 1234'."""
    if not text:
        return None
    cleaned = re.sub(r"[^A-Z0-9]+", " ", str(text).upper()).strip()
    return cleaned or None


# ------------------------------------------------------------------ uploads
def upload_request(body):
    """Validate POST /upload-url. Returns (request, error_code, message)."""
    group = body.get("group")
    if group not in limits.GROUPS:
        return None, "UNSUPPORTED_GROUP", (
            f"group must be one of {', '.join(sorted(limits.GROUPS))}"
        )
    spec = limits.GROUPS[group]

    content_type = str(body.get("contentType") or "").strip().lower()
    if content_type == "image/jpg":
        content_type = "image/jpeg"
    if content_type not in spec["types"]:
        return None, "UNSUPPORTED_TYPE", (
            f"{group} files must be one of {', '.join(sorted(spec['types']))}"
        )

    size = body.get("sizeBytes")
    if isinstance(size, bool) or not isinstance(size, int):
        return None, "INVALID_REQUEST", "sizeBytes must be a whole number of bytes"
    if size <= 0:
        return None, "EMPTY_FILE", "the file is empty"
    if size > spec["maxBytes"]:
        return None, "FILE_TOO_LARGE", (
            f"{group} files can be at most {spec['maxBytes'] // limits.MB} MB"
        )

    role = None
    if group == "photo":
        role = body.get("role") or "current"
        if role not in limits.PHOTO_ROLES:
            return None, "INVALID_ROLE", (
                f"photo role must be one of {', '.join(limits.PHOTO_ROLES)}"
            )
    elif body.get("role") not in (None, ""):
        return None, "INVALID_ROLE", "only photos take a role"

    try:
        filename = clean_text(body.get("filename") or "", 200) or "upload"
    except ValueError:
        return None, "INVALID_REQUEST", "filename must be text"
    filename = re.sub(r"[\\/]", "_", filename)[:120]

    return {
        "group": group,
        "role": role,
        "contentType": content_type,
        "sizeBytes": size,
        "filename": filename,
        "extension": spec["types"][content_type],
    }, None, None


SIGNATURES = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "application/pdf": (b"%PDF-",),
}


def signature_ok(content_type, head):
    """Does the start of the file match the declared type?"""
    if content_type in SIGNATURES:
        return any(head.startswith(magic) for magic in SIGNATURES[content_type])
    if content_type in ("application/json", "application/geo+json"):
        stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
        return stripped[:1] in (b"{", b"[")
    return False


def pdf_page_count(data):
    """Rough page count from the raw PDF: '/Type /Page' objects (not /Pages).

    Good enough to refuse a multi-page slip before paying for Textract, which
    would refuse it anyway. Compressed object streams can hide pages, so a
    count of 0 is treated as 'unknown', not as 'one'.
    """
    return len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", data))


# ------------------------------------------------------------------- traces
class TraceError(ValueError):
    pass


def _point_time(value, index):
    if not isinstance(value, str) or not value.strip():
        raise TraceError(f"point {index + 1} has no timestamp")
    try:
        return iso_time(value)
    except ValueError as exc:
        raise TraceError(f"point {index + 1} timestamp is not ISO 8601") from exc


def _coord(value, name, index, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TraceError(f"point {index + 1} {name} is missing or not a number")
    value = float(value)
    if math.isnan(value) or math.isinf(value) or not low <= value <= high:
        raise TraceError(f"point {index + 1} {name} {value:g} is out of range")
    return value


def _raw_points(payload):
    """Pull (lon, lat, time, vehicleNo) out of the two supported formats."""
    if isinstance(payload, dict) and payload.get("type") in ("Feature", "FeatureCollection"):
        feature = payload
        if payload["type"] == "FeatureCollection":
            features = payload.get("features") or []
            if len(features) != 1:
                raise TraceError("a GeoJSON FeatureCollection must hold exactly one trace")
            feature = features[0]
        geometry = (feature or {}).get("geometry") or {}
        if geometry.get("type") != "LineString":
            raise TraceError("the GeoJSON geometry must be a LineString")
        coordinates = geometry.get("coordinates")
        properties = feature.get("properties") or {}
        times = properties.get("times") or properties.get("coordTimes")
        if not isinstance(coordinates, list):
            raise TraceError("the LineString has no coordinates")
        if not isinstance(times, list) or len(times) != len(coordinates):
            raise TraceError(
                "GeoJSON traces need properties.times with one timestamp per coordinate"
            )
        raw = []
        for index, (coordinate, stamp) in enumerate(zip(coordinates, times)):
            if not isinstance(coordinate, list) or len(coordinate) < 2:
                raise TraceError(f"point {index + 1} is not [longitude, latitude]")
            raw.append((coordinate[0], coordinate[1], stamp))
        return raw, properties.get("vehicleNo"), "geojson"

    if isinstance(payload, dict) and isinstance(payload.get("points"), list):
        raw = []
        for index, point in enumerate(payload["points"]):
            if not isinstance(point, dict):
                raise TraceError(f"point {index + 1} must be an object with lat, lon and t")
            raw.append((point.get("lon"), point.get("lat"), point.get("t", point.get("timestamp"))))
        return raw, payload.get("vehicleNo"), "siltproof"

    raise TraceError(
        "unrecognised trace format: use {\"points\": [{\"lat\", \"lon\", \"t\"}]} "
        "or a GeoJSON LineString Feature with properties.times"
    )


def parse_trace(data):
    """Bytes -> validated trace summary. Raises TraceError with a readable reason."""
    try:
        payload = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise TraceError("the file is not valid UTF-8 JSON") from exc

    raw, vehicle, fmt = _raw_points(payload)

    if len(raw) < limits.TRACE_MIN_POINTS:
        raise TraceError(f"a trace needs at least {limits.TRACE_MIN_POINTS} points")
    if len(raw) > limits.TRACE_MAX_POINTS:
        raise TraceError(f"a trace can have at most {limits.TRACE_MAX_POINTS:,} points")

    points = []
    for index, (lon, lat, stamp) in enumerate(raw):
        lon = _coord(lon, "longitude", index, -180, 180)
        lat = _coord(lat, "latitude", index, -90, 90)
        if lon == 0 and lat == 0:
            raise TraceError(f"point {index + 1} is [0, 0], which is not a real fix")
        when = _point_time(stamp, index)
        points.append({"lon": lon, "lat": lat, "t": when})

    warnings = []
    aware = {point["t"].tzinfo is not None for point in points}
    if len(aware) > 1:
        raise TraceError("timestamps mix timezone offsets with times that have none")
    if aware == {False}:
        warnings.append("Timestamps carry no timezone offset; they are compared as local times.")

    for index in range(1, len(points)):
        if points[index]["t"] < points[index - 1]["t"]:
            raise TraceError(
                f"timestamps go backwards at point {index + 1} "
                f"({points[index]['t'].isoformat()} after {points[index - 1]['t'].isoformat()})"
            )

    duplicates = 0
    fastest = 0.0
    distance = 0.0
    for previous, current in zip(points, points[1:]):
        a, b = [previous["lon"], previous["lat"]], [current["lon"], current["lat"]]
        step = geo.haversine_m(a, b)
        distance += step
        if step == 0:
            duplicates += 1
        seconds = (current["t"] - previous["t"]).total_seconds()
        if seconds > 0:
            fastest = max(fastest, step / seconds * 3.6)
    if duplicates:
        warnings.append(f"{duplicates} consecutive point(s) repeat the previous position.")
    if fastest > limits.TRACE_JUMP_KMH:
        warnings.append(
            f"One step implies {fastest:.0f} km/h, faster than a loaded truck; "
            "the trace may contain a bad fix."
        )

    as_text = [
        {"lat": round(point["lat"], 7), "lon": round(point["lon"], 7),
         "t": point["t"].isoformat()}
        for point in points
    ]
    vehicle = normalise_vehicle(vehicle) if isinstance(vehicle, str) else None

    return {
        "format": fmt,
        "vehicleNo": vehicle,
        "points": as_text,
        "pointCount": len(points),
        "startTime": as_text[0]["t"],
        "endTime": as_text[-1]["t"],
        "distanceM": round(distance, 1),
        "maxSpeedKmh": round(fastest, 1),
        "warnings": warnings,
    }


def swapped_hint(points, reference):
    """True when the trace sits far from the reference but would be near it with
    latitude and longitude swapped: the usual GeoJSON ordering mistake."""
    if not points or not reference:
        return False
    first = [points[0]["lon"], points[0]["lat"]]
    swapped = [points[0]["lat"], points[0]["lon"]]
    if not (-90 <= swapped[1] <= 90):
        return False
    return geo.haversine_m(first, reference) > 500_000 and geo.haversine_m(swapped, reference) < 50_000
