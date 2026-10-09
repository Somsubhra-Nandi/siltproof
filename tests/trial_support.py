"""Helpers for the judge-trial tests: events, uploads and test images.

Uploads are simulated with put_object, because a presigned POST can only be
exercised over HTTP; the policy itself is checked separately.
"""

import datetime
import io
import json
import random

import piexif
from PIL import Image

from api import app

KOLKATA = (88.4712, 22.5801)          # [lon, lat]-ish, approximate, test only
DUMP = (88.4300, 22.5600)


def call(route_key, *, token=None, body=None, **params):
    headers = {"content-type": "application/json"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    event = {
        "routeKey": route_key,
        "headers": headers,
        "pathParameters": params or None,
        "body": json.dumps(body) if body is not None else None,
    }
    response = app.lambda_handler(event, None)
    return response["statusCode"], json.loads(response["body"])


def create_trial(**body):
    status, payload = call("POST /trials", body=body or {"label": "test"})
    assert status == 201, payload
    return payload["trialId"], payload["accessToken"]


def _rational(value):
    value = abs(float(value))
    degrees = int(value)
    minutes_float = (value - degrees) * 60
    minutes = int(minutes_float)
    seconds = round((minutes_float - minutes) * 60, 3)
    return ((degrees, 1), (minutes, 1), (int(seconds * 1000), 1000))


def jpeg(seed=1, *, lat=None, lon=None, stamp="2026:10:09 09:53:12", offset=None,
         model=b"Phone X\x00\x00\x00", size=(320, 240), quality=90, noise=False):
    """A JPEG with real EXIF. Different seeds give perceptually different images."""
    rng = random.Random(seed)
    image = Image.new("RGB", size)
    pixels = image.load()
    blocks = [(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(16)]
    for x in range(size[0]):
        for y in range(size[1]):
            base = blocks[(x * 4 // size[0]) + 4 * (y * 4 // size[1])]
            if noise:
                pixels[x, y] = tuple((c + rng.randrange(-60, 60)) % 256 for c in base)
            else:
                pixels[x, y] = base

    zeroth = {piexif.ImageIFD.Make: b"TestMake", piexif.ImageIFD.Model: model}
    exif = {}
    if stamp:
        exif[piexif.ExifIFD.DateTimeOriginal] = stamp.encode()
    if offset:
        exif[piexif.ExifIFD.OffsetTimeOriginal] = offset.encode()
    gps = {}
    if lat is not None and lon is not None:
        gps = {
            piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
            piexif.GPSIFD.GPSLatitude: _rational(lat),
            piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W",
            piexif.GPSIFD.GPSLongitude: _rational(lon),
            piexif.GPSIFD.GPSAltitude: (0, 1),
        }
    raw = piexif.dump({"0th": zeroth, "Exif": exif, "GPS": gps, "1st": {}, "thumbnail": None})
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=quality, exif=raw)
    return out.getvalue()


def png(seed=1, size=(200, 160)):
    rng = random.Random(seed)
    image = Image.new("RGB", size, (rng.randrange(256), rng.randrange(256), rng.randrange(256)))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def minimal_pdf(pages=1):
    objects = ["<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + index} 0 R" for index in range(pages))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>")
    for _ in range(pages):
        objects.append("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>")
    body = "%PDF-1.4\n"
    for index, obj in enumerate(objects, start=1):
        body += f"{index} 0 obj\n{obj}\nendobj\n"
    body += "trailer\n<< /Root 1 0 R >>\n%%EOF\n"
    return body.encode("ascii")


def trace_json(points, vehicle="WB 00 MK 0001"):
    return json.dumps({"vehicleNo": vehicle, "points": points}).encode("utf-8")


def drive(start, end, start_time, minutes, steps=12, dwell_at_end=3):
    """Points from start to end over `minutes`, then a few fixes at the end."""
    t0 = datetime.datetime.fromisoformat(start_time)
    points = []
    for index in range(steps + 1):
        fraction = index / steps
        points.append({
            "lon": start[0] + (end[0] - start[0]) * fraction,
            "lat": start[1] + (end[1] - start[1]) * fraction,
            "t": (t0 + datetime.timedelta(minutes=minutes * fraction)).isoformat(),
        })
    last = datetime.datetime.fromisoformat(points[-1]["t"])
    for index in range(1, dwell_at_end + 1):
        points.append({"lon": end[0], "lat": end[1],
                       "t": (last + datetime.timedelta(minutes=index)).isoformat()})
    return points


def upload(aws, trial_id, token, group, data, content_type, *, role=None,
           filename="file", put_type=None, put_data=None, complete=True):
    """Register, 'upload' with put_object, and complete. Returns (status, payload, evid)."""
    body = {"group": group, "filename": filename, "contentType": content_type,
            "sizeBytes": len(data)}
    if role:
        body["role"] = role
    status, payload = call("POST /trials/{trialId}/upload-url", token=token, body=body,
                           trialId=trial_id)
    assert status == 201, payload
    evidence_id = payload["evidenceId"]
    key = payload["upload"]["fields"]["key"]
    aws["s3"].put_object(Bucket=aws["bucket"], Key=key,
                         Body=put_data if put_data is not None else data,
                         ContentType=put_type or content_type)
    if not complete:
        return 201, payload, evidence_id
    status, payload = call("POST /trials/{trialId}/evidence/{evidenceId}/complete",
                           token=token, trialId=trial_id, evidenceId=evidence_id)
    return status, payload, evidence_id
