#!/usr/bin/env python3
"""Generate stand-in evidence photos with real EXIF GPS and timestamps.

    python data/gen_photos.py

These exist so the whole pipeline - EXIF, pHash, Bedrock vision, rules R1-R4 -
is testable before the photo shoot. Replace them with the real photos using
seed.py --photos-dir and --photo-map (see data/PHOTO_MAPPING.md); nothing else
changes.

What it writes, per plan section 6:
  * a before and an after photo for each of the 18 drains, with EXIF GPS
    inside that drain's geofence and a timestamp inside the work window
  * a silt load photo and a construction-debris load photo
  * drain 8's after photo deliberately 45 m outside the geofence (R1, soft)
  * drain 14's two after photos reused from drain 9: one an exact copy, one
    cropped and brightened, so pHash catches edited reuse as well as a
    straight duplicate (R3)

Output: data/out/evidence/photos/B1/drain<id>/<role>-<nn>.jpg
        data/out/mock_manifest.json  (what Bedrock should say about each photo)
"""

import argparse
import datetime
import io
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import dataset as ds  # noqa: E402

import piexif  # noqa: E402
from PIL import Image, ImageDraw, ImageEnhance  # noqa: E402

SIZE = (900, 675)

# How far outside its geofence drain 8's photo sits: inside the soft band
# (30-60 m) so the drain reads amber, as the plan's table wants.
R8_OUTSIDE_M = 45.0

ROLE_PALETTES = {
    "before": ((96, 84, 60), (62, 54, 38), "Silted channel"),
    "after": ((120, 124, 118), (86, 92, 88), "Cleared channel"),
    "load": ((78, 70, 52), (54, 48, 36), "Tipper load"),
}


def to_dms_rational(value):
    """Decimal degrees -> EXIF ((d,1),(m,1),(s,100)) rationals."""
    value = abs(float(value))
    degrees = int(value)
    minutes_float = (value - degrees) * 60
    minutes = int(minutes_float)
    seconds = round((minutes_float - minutes) * 60, 2)
    return ((degrees, 1), (minutes, 1), (int(seconds * 100), 100))


def exif_bytes(lat, lon, stamp):
    """A minimal but real EXIF block: GPS, DateTimeOriginal and a camera."""
    date_text = stamp.strftime("%Y:%m:%d %H:%M:%S")
    offset = stamp.strftime("%z")
    offset_text = f"{offset[:3]}:{offset[3:]}" if offset else "+05:30"

    zeroth = {
        piexif.ImageIFD.Make: b"SiltProof",
        piexif.ImageIFD.Model: b"Simulated Evidence Camera",
        piexif.ImageIFD.DateTime: date_text.encode(),
        piexif.ImageIFD.Software: b"siltproof gen_photos.py",
    }
    exif = {
        piexif.ExifIFD.DateTimeOriginal: date_text.encode(),
        piexif.ExifIFD.DateTimeDigitized: date_text.encode(),
        piexif.ExifIFD.OffsetTimeOriginal: offset_text.encode(),
    }
    gps = {
        piexif.GPSIFD.GPSVersionID: (2, 3, 0, 0),
        piexif.GPSIFD.GPSLatitudeRef: (b"N" if lat >= 0 else b"S"),
        piexif.GPSIFD.GPSLatitude: to_dms_rational(lat),
        piexif.GPSIFD.GPSLongitudeRef: (b"E" if lon >= 0 else b"W"),
        piexif.GPSIFD.GPSLongitude: to_dms_rational(lon),
        piexif.GPSIFD.GPSAltitudeRef: 0,
        piexif.GPSIFD.GPSAltitude: (1200, 100),
    }

    return piexif.dump({"0th": zeroth, "Exif": exif, "GPS": gps, "1st": {}, "thumbnail": None})


def draw_scene(role, label, random, debris=False):
    """A recognisable stand-in image: not a real photo, clearly a placeholder.

    Every scene is laid out differently - channel position and width, horizon,
    tone, and a handful of large patches - because pHash compares the coarse
    luminance structure of an image. Scenes drawn from one fixed template all
    hash alike, which would make rule R3 call every photo a duplicate of every
    other. The variation here is what keeps unrelated photos far apart.
    """
    top, bottom, caption = ROLE_PALETTES[role]
    shift = random.randint(-28, 28)
    top = tuple(max(0, min(255, channel + shift)) for channel in top)
    bottom = tuple(max(0, min(255, channel + shift)) for channel in bottom)

    image = Image.new("RGB", SIZE, top)
    draw = ImageDraw.Draw(image)

    # Sky-to-ground gradient, at a slight angle so even this differs.
    tilt = random.randint(-120, 120)
    for y in range(SIZE[1]):
        fraction = y / SIZE[1]
        colour = tuple(
            int(top[channel] + (bottom[channel] - top[channel]) * fraction) for channel in range(3)
        )
        draw.line([(0, y), (SIZE[0], y + tilt)], fill=colour)

    if role in ("before", "after"):
        left = random.randint(190, 360)
        right = random.randint(520, 720)
        horizon = random.randint(210, 330)
        wall_left = tuple(random.randint(78, 140) for _ in range(3))
        wall_right = tuple(random.randint(78, 140) for _ in range(3))

        draw.polygon([(0, horizon + 140), (left, horizon), (left, 675), (0, 675)], fill=wall_left)
        draw.polygon([(900, horizon + 140), (right, horizon), (right, 675), (900, 675)], fill=wall_right)

        if role == "before":
            silt = tuple(random.randint(52, 92) for _ in range(3))
            draw.polygon([(left, 675), (left, horizon + 40), (right, horizon + 40), (right, 675)], fill=silt)
            for _ in range(140):
                x = random.randint(left + 5, right - 5)
                y = random.randint(horizon + 45, 670)
                size = random.randint(3, 12)
                draw.ellipse([x, y, x + size, y + size], fill=tuple(max(0, c - 14) for c in silt))
        else:
            bed = tuple(random.randint(104, 150) for _ in range(3))
            draw.polygon([(left, 675), (left, horizon + 40), (right, horizon + 40), (right, 675)], fill=bed)
            water_left = left + random.randint(30, 90)
            water_right = right - random.randint(30, 90)
            draw.polygon(
                [(water_left, 675), (water_left, horizon + 70), (water_right, horizon + 70), (water_right, 675)],
                fill=(random.randint(70, 104), random.randint(96, 126), random.randint(110, 142)),
            )
    else:
        body_top = random.randint(370, 430)
        draw.rectangle([random.randint(90, 150), body_top, random.randint(740, 800), 620],
                       fill=tuple(random.randint(56, 92) for _ in range(3)))
        heap = [(160, body_top)]
        peak = random.randint(110, 190)
        for step in range(1, 24):
            x = 160 + step * 28
            y = body_top - int(peak * math.sin(step / 24 * math.pi)) + random.randint(-12, 12)
            heap.append((x, y))
        heap.append((740, body_top))
        draw.polygon(heap, fill=(168, 148, 134) if debris else (96, 72, 48))

        if debris:
            # Angular brick and concrete, visibly not silt.
            for _ in range(90):
                x = random.randint(175, 720)
                y = random.randint(body_top - peak, body_top - 5)
                w = random.randint(14, 44)
                h = random.randint(10, 26)
                colour = random.choice([(150, 72, 54), (182, 176, 168), (128, 124, 118)])
                draw.polygon(
                    [(x, y), (x + w, y - random.randint(0, 8)), (x + w - 6, y + h), (x - 4, y + h - 4)],
                    fill=colour,
                )
        else:
            for _ in range(180):
                x = random.randint(175, 720)
                y = random.randint(body_top - peak, body_top - 5)
                size = random.randint(4, 13)
                draw.ellipse([x, y, x + size, y + size], fill=(78, 60, 40))

    # Large patches - vegetation, puddles, shade. These dominate the low
    # frequencies pHash looks at, so they are what makes each scene distinct.
    for _ in range(random.randint(4, 7)):
        cx = random.randint(0, SIZE[0])
        cy = random.randint(60, SIZE[1])
        rx = random.randint(70, 240)
        ry = random.randint(40, 150)
        draw.ellipse(
            [cx - rx, cy - ry, cx + rx, cy + ry],
            fill=tuple(random.randint(30, 190) for _ in range(3)),
        )

    draw.rectangle([0, 0, SIZE[0], 34], fill=(20, 20, 20))
    draw.text((12, 9), f"SIMULATED EVIDENCE  |  {label}  |  {caption}", fill=(235, 235, 235))

    return image


def save_jpeg(image, path, lat, lon, stamp, quality=88):
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    piexif.insert(exif_bytes(lat, lon, stamp), buffer.getvalue(), str(path))


def point_inside(ring, random, centreline=None):
    """A point on the drain's centreline, jittered a few metres."""
    if centreline and len(centreline) >= 2:
        index = random.randrange(len(centreline) - 1)
        base = ds.geo.interpolate(
            centreline[index], centreline[index + 1], random.random()
        )
    else:
        base = [
            sum(p[0] for p in ring) / len(ring),
            sum(p[1] for p in ring) / len(ring),
        ]

    return ds.geo.offset_m(base, random.uniform(-8, 8), random.uniform(-8, 8))


def point_outside(centreline, metres, random):
    """A point a set distance to the side of the drain's centreline."""
    a, b = centreline[0], centreline[min(1, len(centreline) - 1)]
    bearing = math.atan2(b[1] - a[1], b[0] - a[0]) + math.pi / 2
    base = ds.geo.interpolate(a, b, 0.5)
    return ds.geo.offset_m(base, math.cos(bearing) * metres, math.sin(bearing) * metres)


def work_window_stamp(drain_index, role_index, random):
    start = datetime.datetime.fromisoformat(ds.WORK_WINDOW[0])
    return start + datetime.timedelta(
        days=(drain_index + role_index) % 10,
        hours=random.randint(2, 9),
        minutes=random.randint(0, 59),
        seconds=random.randint(0, 59),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--drains", default=None)
    parser.add_argument("--seed", default="siltproof")
    args = parser.parse_args(argv)

    drains_path = pathlib.Path(args.drains or (ds.OUT / "drains.geojson"))
    if not drains_path.exists():
        print(f"No drains file at {ds.relative(drains_path)}. Run data/osm_drains.py first.")
        return 2

    collection = ds.load_json(drains_path)
    features = sorted(collection["features"], key=lambda f: int(f["properties"]["drainId"]))

    out_root = ds.OUT / "evidence"
    manifest_path = ds.OUT / "mock_manifest.json"
    manifest = ds.load_json(manifest_path) if manifest_path.exists() else {}
    manifest.setdefault("photos", {})
    photo_index = {}

    ds.banner("SiltProof stand-in photos")

    written = 0
    for drain_index, feature in enumerate(features):
        properties = feature["properties"]
        drain_id = properties["drainId"]
        ring = feature["geometry"]["coordinates"][0]
        centreline = properties.get("centreline") or []
        random = ds.rng(args.seed, f"photos-{drain_id}")

        for role_index, role in enumerate(("before", "after")):
            # Drain 8's after photo is the one taken outside the geofence.
            outside = drain_id == "8" and role == "after"
            if outside and centreline:
                lon, lat = point_outside(centreline, R8_OUTSIDE_M, random)
            else:
                lon, lat = point_inside(ring, random, centreline)

            stamp = work_window_stamp(drain_index, role_index, random)
            image = draw_scene(role, f"drain {drain_id} {role}", random)

            key = ds.photo_key(drain_id, role, 1)
            save_jpeg(image, out_root / key, lat, lon, stamp)
            photo_index[(drain_id, role)] = {"key": key, "lat": lat, "lon": lon, "stamp": stamp}

            manifest["photos"][key] = {
                "drainId": drain_id,
                "role": role,
                "cleared": role == "after",
                "loadType": "unclear",
                "confidence": 0.9 if role == "after" else 0.86,
                "notes": (
                    "Channel bed and invert are visible; the section looks cleared."
                    if role == "after"
                    else "Channel is full of wet silt up to the kerb line."
                ),
                "outsideGeofence": outside,
            }
            written += 1

    # ---- load photos: one silt, one construction debris (drain 3's case)
    random = ds.rng(args.seed, "loads")
    for drain_id, debris in (("9", False), ("3", True)):
        feature = next(f for f in features if f["properties"]["drainId"] == drain_id)
        centreline = feature["properties"].get("centreline") or []
        lon, lat = point_inside(feature["geometry"]["coordinates"][0], random, centreline)
        stamp = work_window_stamp(int(drain_id), 2, random)

        image = draw_scene("load", f"drain {drain_id} load", random, debris=debris)
        key = ds.photo_key(drain_id, "load", 1)
        save_jpeg(image, out_root / key, lat, lon, stamp)

        manifest["photos"][key] = {
            "drainId": drain_id,
            "role": "load",
            "cleared": True,
            "loadType": "debris" if debris else "silt",
            "confidence": 0.88 if debris else 0.93,
            "notes": (
                "Load is broken brick, concrete rubble and tile fragments, not drain silt."
                if debris
                else "Load is wet grey-black drain silt."
            ),
        }
        written += 1

    # ---- drain 14's reused photos (R3), copied from drain 9's after photo
    source_meta = photo_index[("9", "after")]
    source_path = out_root / source_meta["key"]
    source = Image.open(source_path)

    reuse_random = ds.rng(args.seed, "reuse")
    feature14 = next(f for f in features if f["properties"]["drainId"] == "14")
    centreline14 = feature14["properties"].get("centreline") or []
    lon14, lat14 = point_inside(feature14["geometry"]["coordinates"][0], reuse_random, centreline14)
    stamp14 = work_window_stamp(14, 1, reuse_random)

    # 1. an exact duplicate: copy the encoded bytes and only swap the EXIF, so
    # the pixels really are identical rather than re-compressed.
    exact_key = ds.photo_key("14", "after", 2)
    exact_path = out_root / exact_key
    exact_path.parent.mkdir(parents=True, exist_ok=True)
    piexif.insert(exif_bytes(lat14, lon14, stamp14), source_path.read_bytes(), str(exact_path))

    # 2. the same photo cropped and brightened, which pHash still catches
    cropped = source.crop((26, 20, SIZE[0] - 26, SIZE[1] - 20)).resize(SIZE)
    edited = ImageEnhance.Brightness(cropped).enhance(1.18)
    edited_key = ds.photo_key("14", "after", 3)
    save_jpeg(edited, out_root / edited_key, lat14, lon14, stamp14 + datetime.timedelta(minutes=12))

    for key, how in ((exact_key, "exact copy"), (edited_key, "cropped and brightened copy")):
        manifest["photos"][key] = {
            "drainId": "14",
            "role": "after",
            "cleared": True,
            "loadType": "unclear",
            "confidence": 0.9,
            "notes": "Channel bed and invert are visible; the section looks cleared.",
            "reusedFrom": source_meta["key"],
            "reuseKind": how,
        }
        written += 1

    source.close()
    ds.save_json(manifest_path, manifest)

    print(f"photos      {written} written under {ds.relative(out_root)}/photos")
    print(f"drain 8     after photo {R8_OUTSIDE_M:.0f} m outside its geofence (R1, soft band)")
    print(f"drain 3     load photo is construction debris (R4)")
    print(f"drain 14    two after photos reused from drain 9: exact copy and edited copy (R3)")
    print(f"manifest    {ds.relative(manifest_path)}")
    print("\nThese are stand-ins. Swap in the real shoot with:")
    print("  python data/seed.py --photos-dir <folder> --photo-map data/photo_map.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
