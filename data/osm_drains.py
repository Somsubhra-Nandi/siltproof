#!/usr/bin/env python3
"""Build the ward's 18 drain geofences.

Real geometry comes from OpenStreetMap via Overpass (plan section 6):

    python data/osm_drains.py --center 19.0760,72.8777 --radius 2500
    python data/osm_drains.py --bbox 19.05,72.86,19.10,72.90

Offline, for tests and for building the rest of the pipeline before the ward is
chosen:

    python data/osm_drains.py --synthetic --center 19.0760,72.8777

Both modes write the same shape: a FeatureCollection of 18 buffered polygons
with length, width and depth, plus the approved dump site, so nothing
downstream knows or cares which mode produced it.

No city is hard-coded: --center or --bbox is required.
"""

import argparse
import json
import math
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

import dataset as ds  # noqa: E402

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT_S = 90

# Overpass QL. waterway=drain|canal|ditch, as the plan specifies.
OVERPASS_QUERY = """
[out:json][timeout:{timeout}];
(
  way["waterway"~"^(drain|canal|ditch)$"]({south},{west},{north},{east});
);
out geom;
"""

# Plausible cross-sections for a municipal storm-water drain, metres.
WIDTH_CHOICES = [1.2, 1.5, 1.8, 2.0, 2.5, 3.0, 3.5]
DEPTH_CHOICES = [0.8, 1.0, 1.2, 1.5, 1.8]


# --------------------------------------------------------------- geometry
def polyline_length_m(line):
    return sum(
        ds.geo.haversine_m(line[index], line[index + 1]) for index in range(len(line) - 1)
    )


def buffer_line(line, radius_m, origin, segments=6):
    """Buffer a [lon, lat] polyline into a GeoJSON Polygon ring.

    Projects to local metres around the ward centre, buffers with shapely, and
    projects back. Over one ward the flat approximation costs centimetres
    (DECISIONS.md), and it keeps pyproj out of the repo.
    """
    from shapely.geometry import LineString

    local = LineString([ds.geo.to_local_m(point, origin) for point in line])
    # Round caps and joins: the geofence should extend 30 m past the ends of
    # the section, and mitred joins can spike metres out on a sharp bend.
    buffered = local.buffer(radius_m, quad_segs=segments)

    if buffered.is_empty:
        return None

    # A buffered line is a single polygon; take its exterior.
    exterior = list(buffered.exterior.coords)
    ring = [ds.geo.from_local_m(point, origin) for point in exterior]

    if ring[0] != ring[-1]:
        ring.append(ring[0])

    return [[round(lon, 7), round(lat, 7)] for lon, lat in ring]


def square_polygon(center, half_side_m):
    """A square geofence around a point, for the dump site."""
    ring = [
        ds.geo.offset_m(center, -half_side_m, -half_side_m),
        ds.geo.offset_m(center, half_side_m, -half_side_m),
        ds.geo.offset_m(center, half_side_m, half_side_m),
        ds.geo.offset_m(center, -half_side_m, half_side_m),
    ]
    ring.append(ring[0])
    return [[round(lon, 7), round(lat, 7)] for lon, lat in ring]


# ----------------------------------------------------------------- sources
def fetch_overpass(bbox, timeout=OVERPASS_TIMEOUT_S):
    """bbox is (south, west, north, east). Returns the raw Overpass JSON."""
    south, west, north, east = bbox
    query = OVERPASS_QUERY.format(
        timeout=timeout, south=south, west=west, north=north, east=east
    )

    request = urllib.request.Request(
        OVERPASS_URL,
        data=query.encode("utf-8"),
        headers={"User-Agent": "siltproof-hackathon/1.0", "Content-Type": "text/plain"},
    )

    print(f"Overpass: querying {OVERPASS_URL} for bbox {bbox}")
    with urllib.request.urlopen(request, timeout=timeout + 15) as response:
        return json.loads(response.read().decode("utf-8"))


def lines_from_overpass(payload):
    """Overpass elements -> [{osmId, name, line}, ...] sorted longest first."""
    lines = []

    for element in payload.get("elements", []):
        if element.get("type") != "way":
            continue

        geometry = element.get("geometry") or []
        line = [
            [float(point["lon"]), float(point["lat"])]
            for point in geometry
            if point.get("lat") is not None and point.get("lon") is not None
        ]
        if len(line) < 2:
            continue

        tags = element.get("tags") or {}
        lines.append(
            {
                "osmId": element.get("id"),
                "name": tags.get("name"),
                "waterway": tags.get("waterway"),
                "line": line,
                "lengthM": round(polyline_length_m(line), 1),
            }
        )

    lines.sort(key=lambda item: item["lengthM"], reverse=True)
    return lines


def split_long_lines(lines, wanted, max_length_m=600.0):
    """Cut over-long ways into sections so 18 sections are available.

    A single OSM canal can run for kilometres; a ward's desilting bill is
    itemised by section, so long ways are chopped into pieces of at most
    max_length_m.
    """
    sections = []

    for item in lines:
        line = item["line"]
        total = item["lengthM"]
        pieces = max(1, math.ceil(total / max_length_m))

        if pieces == 1:
            sections.append(item)
            continue

        # Split by evenly divided indices, with one point of overlap so the
        # sections join up. Dividing by a floored piece length instead would
        # dump every leftover point into the last section, leaving it several
        # times longer than the rest.
        for index in range(pieces):
            start = index * len(line) // pieces
            end = min(len(line), (index + 1) * len(line) // pieces + 1)
            piece = line[start:end]
            if len(piece) < 2:
                continue
            sections.append(
                {
                    "osmId": item["osmId"],
                    "name": item["name"],
                    "waterway": item["waterway"],
                    "line": piece,
                    "lengthM": round(polyline_length_m(piece), 1),
                    "section": index + 1,
                }
            )

        if len(sections) >= wanted * 3:
            break

    sections.sort(key=lambda item: item["lengthM"], reverse=True)
    return sections


def synthetic_lines(center, count, seed):
    """Fake but plausible drain centrelines radiating around a centre."""
    random = ds.rng(seed, "synthetic-drains")
    lines = []

    for index in range(count):
        angle = (index / count) * 2 * math.pi + random.uniform(-0.12, 0.12)
        start_radius = random.uniform(350, 1700)
        length = random.uniform(180, 520)

        start = ds.geo.offset_m(
            center,
            math.cos(angle) * start_radius,
            math.sin(angle) * start_radius,
        )

        # Walk outward in three slightly bent segments, so the buffered polygon
        # looks like a drain rather than a rectangle.
        line = [start]
        heading = angle + random.uniform(-0.5, 0.5)
        for _ in range(3):
            heading += random.uniform(-0.35, 0.35)
            step = length / 3
            line.append(
                ds.geo.offset_m(line[-1], math.cos(heading) * step, math.sin(heading) * step)
            )

        lines.append(
            {
                "osmId": None,
                "name": None,
                "waterway": random.choice(["drain", "canal", "ditch"]),
                "line": [[round(lon, 7), round(lat, 7)] for lon, lat in line],
                "lengthM": round(polyline_length_m(line), 1),
                "synthetic": True,
            }
        )

    return lines


# ----------------------------------------------------------------- assembly
def build_features(sections, center, seed, buffer_m):
    random = ds.rng(seed, "drain-attributes")
    features = []

    for index, section in enumerate(sections, start=1):
        drain_id = str(index)
        ring = buffer_line(section["line"], buffer_m, center)
        if ring is None:
            continue

        width = random.choice(WIDTH_CHOICES)
        depth = random.choice(DEPTH_CHOICES)
        name = section.get("name") or f"{(section.get('waterway') or 'drain').title()} section {drain_id}"

        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [ring]},
                "properties": {
                    "drainId": drain_id,
                    "name": name,
                    "waterway": section.get("waterway"),
                    "osmId": section.get("osmId"),
                    "lengthM": section["lengthM"],
                    "widthM": width,
                    "depthM": depth,
                    "bufferM": buffer_m,
                    "claimedTonnes": ds.DRAIN_CLAIMED.get(drain_id),
                    # R10's ceiling, so the rule has nothing to recompute.
                    "plausibleMaxTonnes": round(
                        section["lengthM"] * width * depth * ds.SILT_DENSITY * 1.2, 1
                    ),
                    "centreline": section["line"],
                    "synthetic": bool(section.get("synthetic")),
                },
            }
        )

    return features


def build_dumpsite(center, dumpsite_point, half_side_m=220.0):
    point = dumpsite_point or ds.geo.offset_m(center, 5700, 5700)
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [square_polygon(point, half_side_m)],
                },
                "properties": {
                    "dumpsiteId": "D1",
                    "name": "Approved dumping ground",
                    "center": [round(point[0], 7), round(point[1], 7)],
                    "halfSideM": half_side_m,
                    "placeholder": dumpsite_point is None,
                },
            }
        ],
    }


def bbox_from_center(center, radius_m):
    lon, lat = center
    dlat = radius_m / ds.geo.M_PER_DEG_LAT
    dlon = radius_m / ds.geo.m_per_deg_lon(lat)
    return (
        round(lat - dlat, 6), round(lon - dlon, 6),
        round(lat + dlat, 6), round(lon + dlon, 6),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_argument_group("ward location (one is required)")
    source.add_argument("--center", help="ward centre as 'lat,lon'")
    source.add_argument("--bbox", help="'south,west,north,east' in degrees")
    source.add_argument("--radius", type=float, default=2000.0,
                        help="half-size of the box around --center, metres (default 2000)")

    parser.add_argument("--synthetic", action="store_true",
                        help="invent 18 drains around --center instead of querying Overpass")
    parser.add_argument("--count", type=int, default=ds.DRAIN_COUNT)
    parser.add_argument("--buffer", type=float, default=ds.DRAIN_BUFFER_M,
                        help="geofence buffer in metres (default 30)")
    parser.add_argument("--dumpsite", help="approved dump site as 'lat,lon' (default: 8 km NE of centre)")
    parser.add_argument("--seed", default="siltproof")
    parser.add_argument("--out", default=None, help="output GeoJSON (default data/out/drains.geojson)")
    parser.add_argument("--overpass-json", help="read a saved Overpass response instead of querying")

    args = parser.parse_args(argv)

    if not args.center and not args.bbox:
        parser.error("pass --center 'lat,lon' or --bbox 'south,west,north,east'")

    if args.bbox:
        parts = [float(value) for value in args.bbox.split(",")]
        if len(parts) != 4:
            parser.error("--bbox must be 'south,west,north,east'")
        south, west, north, east = parts
        bbox = (south, west, north, east)
        center = [(west + east) / 2, (south + north) / 2]
    else:
        center = ds.parse_lat_lon(args.center, "--center")
        bbox = bbox_from_center(center, args.radius)

    dumpsite_point = ds.parse_lat_lon(args.dumpsite, "--dumpsite") if args.dumpsite else None

    ds.banner("SiltProof drain geofences")
    print(f"centre      {center[1]:.5f}, {center[0]:.5f}")
    print(f"bbox        {bbox}")
    print(f"mode        {'synthetic' if args.synthetic else 'OpenStreetMap via Overpass'}")

    if args.synthetic:
        sections = synthetic_lines(center, args.count, args.seed)
    else:
        if args.overpass_json:
            payload = ds.load_json(args.overpass_json)
        else:
            try:
                payload = fetch_overpass(bbox)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                print(f"\nOverpass failed: {exc}")
                print("Retry, or use --synthetic to keep working offline.")
                return 2

        lines = lines_from_overpass(payload)
        print(f"ways        {len(lines)} returned by Overpass")

        if not lines:
            print("\nNo waterway=drain|canal|ditch in that box. Widen --radius or move --center.")
            return 2

        sections = split_long_lines(lines, args.count)
        print(f"sections    {len(sections)} after splitting long ways")

        if len(sections) < args.count:
            print(f"\nOnly {len(sections)} sections available, {args.count} wanted.")
            print("Widen --radius, or lower --count.")
            return 2

    sections = sections[: args.count]
    features = build_features(sections, center, args.seed, args.buffer)

    collection = {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "billId": ds.BILL_ID,
            "center": [round(center[0], 7), round(center[1], 7)],
            "bbox": list(bbox),
            "bufferM": args.buffer,
            "source": "synthetic" if args.synthetic else "openstreetmap",
            "note": "Geometry from OpenStreetMap contributors, ODbL."
            if not args.synthetic
            else "Synthetic geometry for offline testing. Not real drains.",
        },
    }

    out = args.out or (ds.ensure_out() / "drains.geojson")
    ds.save_json(out, collection)

    dumpsite = build_dumpsite(center, dumpsite_point)
    dumpsite_out = ds.ensure_out() / "dumpsite.geojson"
    ds.save_json(dumpsite_out, dumpsite)

    total_claimed = sum(f["properties"]["claimedTonnes"] or 0 for f in features)
    print(f"\nwrote       {ds.relative(out)}  ({len(features)} drains, {total_claimed} t claimed)")
    print(f"wrote       {ds.relative(dumpsite_out)}")
    if dumpsite_point is None:
        print("\nThe dump site is a placeholder 8 km north-east of the centre.")
        print("Re-run with --dumpsite 'lat,lon' once the real dumping ground is chosen.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
