#!/usr/bin/env python3
"""Check a folder of photos before seeding: EXIF, GPS and which drain they hit.

    python data/check_photos.py ~/siltproof-photos

Entirely offline. Run it on the morning of the shoot, before anything is
uploaded: a photo that lost its EXIF cannot be checked by rules R1 or R2, and
the usual cause is sending it through WhatsApp (plan section 6).
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import dataset as ds  # noqa: E402

from common import photo  # noqa: E402

EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}


def load_drains(path):
    if not pathlib.Path(path).exists():
        return []
    collection = ds.load_json(path)
    return [
        {
            "drainId": feature["properties"]["drainId"],
            "geometry": feature["geometry"],
            "centreline": feature["properties"].get("centreline") or [],
        }
        for feature in collection["features"]
    ]


def locate(point, drains):
    """Which drain the point is inside, or the nearest one and how far."""
    for drain in drains:
        if ds.geo.point_in_geometry(point, drain["geometry"]):
            return drain["drainId"], 0.0

    nearest, best = None, float("inf")
    for drain in drains:
        if not drain["centreline"]:
            continue
        distance = ds.geo.distance_to_line_m(point, drain["centreline"])
        if distance < best:
            nearest, best = drain["drainId"], distance

    return nearest, best


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder")
    parser.add_argument("--drains", default=None, help="default data/out/drains.geojson")
    args = parser.parse_args(argv)

    folder = pathlib.Path(args.folder).expanduser()
    if not folder.is_dir():
        print(f"Not a folder: {folder}")
        return 2

    drains = load_drains(args.drains or (ds.OUT / "drains.geojson"))
    files = sorted(
        path for path in folder.iterdir() if path.suffix.lower() in EXTENSIONS
    )

    if not files:
        print(f"No photos in {folder}")
        return 2

    ds.banner(f"EXIF check: {len(files)} photos in {folder}")
    if not drains:
        print("No drains.geojson yet, so geofences are not checked.\n")

    no_exif, no_gps, outside, hashes = [], [], [], {}

    for path in files:
        data = path.read_bytes()
        exif = photo.read_exif(data)
        digest = photo.perceptual_hash(data)

        if "no_exif" in exif["problems"]:
            no_exif.append(path.name)
            print(f"  {path.name:<28} NO EXIF AT ALL - was this sent over WhatsApp?")
            continue

        if not exif["hasGps"]:
            no_gps.append(path.name)
            print(f"  {path.name:<28} no GPS  time {exif['timestamp']}")
            continue

        drain_id, distance = locate([exif["lon"], exif["lat"]], drains) if drains else (None, None)

        if drains and distance:
            outside.append((path.name, drain_id, distance))
            where = f"OUTSIDE every geofence, {distance:.0f} m from drain {drain_id}"
        elif drains:
            where = f"inside drain {drain_id}"
        else:
            where = ""

        print(f"  {path.name:<28} {exif['lat']:.5f},{exif['lon']:.5f}  {exif['timestamp']}  {where}")

        duplicate = hashes.get(digest)
        if duplicate:
            print(f"  {'':<28} ^ identical to {duplicate} (pHash)")
        hashes[digest] = path.name

    print()
    print(f"  with GPS and time : {len(files) - len(no_exif) - len(no_gps)}")
    print(f"  no EXIF           : {len(no_exif)}")
    print(f"  no GPS            : {len(no_gps)}")
    if drains:
        print(f"  outside geofences : {len(outside)}")
        near = [row for row in outside if row[2] <= 60]
        if near:
            print(f"    of those, {len(near)} are within the 60 m soft band (rule R1 soft fail)")

    if no_exif:
        print("\nPhotos with no EXIF cannot be verified. Re-copy the originals by")
        print("cable, AirDrop or Drive, never through a messaging app.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
