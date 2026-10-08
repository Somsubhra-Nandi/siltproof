#!/usr/bin/env python3
"""Fetch a small local basemap so the map has context with no tile server.

    python data/osm_basemap.py                      # bbox from drains.geojson
    python data/osm_basemap.py --center 19.076,72.8777 --radius 2500
    python data/osm_basemap.py --synthetic          # no network needed

Writes frontend/public/data/basemap.geojson: the ward's roads and water,
simplified, as one GeoJSON file the frontend renders itself. That is what lets
the offline fallback style show streets without contacting a tile server.

It is only a backdrop. The map works without it - the fallback style just draws
a plain background - so a failed fetch is never fatal.

Road and water geometry from OpenStreetMap contributors, ODbL.
"""

import argparse
import json
import math
import pathlib
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import dataset as ds  # noqa: E402

# The main Overpass instance is frequently overloaded and answers 504, so
# try the public mirrors in turn before giving up.
OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
OVERPASS_TIMEOUT_S = 90

# Roads worth drawing, split by how wide to draw them.
MAJOR_ROADS = {"motorway", "trunk", "primary", "secondary", "motorway_link", "trunk_link"}
MINOR_ROADS = {"tertiary", "residential", "unclassified", "living_street", "tertiary_link"}

# Two tiers. Every street around the drains, where the engineer is actually
# looking; only the main roads and water across the wider box out to the dump
# site, which is several kilometres away and would otherwise pull in a few MB
# of residential lanes nobody will look at.
OVERPASS_QUERY = """
[out:json][timeout:{timeout}];
(
  way["highway"~"^(motorway|trunk|primary|secondary|tertiary|residential|unclassified|living_street|motorway_link|trunk_link|tertiary_link)$"]({tight});
  way["highway"~"^(motorway|trunk|primary|secondary|motorway_link|trunk_link)$"]({wide});
  way["natural"="water"]({wide});
  way["waterway"~"^(river|canal)$"]({wide});
  way["landuse"~"^(reservoir|basin)$"]({wide});
);
out geom;
"""

# Simplification tolerance in metres. At ward zoom nobody can see 10 m of
# wiggle, and it keeps the shipped file small.
SIMPLIFY_M = 12.0
MIN_LENGTH_M = 40.0

# ~1 m of precision. This is a backdrop, not evidence, and five decimals
# instead of six takes a tenth off the file the browser has to download.
COORD_DECIMALS = 5

DEFAULT_MARGIN_M = 700.0
OUT = ds.REPO / "frontend" / "public" / "data" / "basemap.geojson"


def simplify(line, origin, tolerance_m=SIMPLIFY_M):
    """Douglas-Peucker in local metres, then back to lon/lat."""
    from shapely.geometry import LineString

    if len(line) < 3:
        return line

    local = LineString([ds.geo.to_local_m(point, origin) for point in line])
    thinned = local.simplify(tolerance_m, preserve_topology=False)

    return [
        [round(lon, COORD_DECIMALS), round(lat, COORD_DECIMALS)]
        for lon, lat in (ds.geo.from_local_m(point, origin) for point in thinned.coords)
    ]


def length_m(line):
    return sum(
        ds.geo.haversine_m(line[index], line[index + 1]) for index in range(len(line) - 1)
    )


def fetch_overpass(tight, wide=None, timeout=OVERPASS_TIMEOUT_S, mirrors=OVERPASS_MIRRORS):
    """Each bbox is (south, west, north, east). Tries each mirror in turn."""
    wide = wide or tight
    query = OVERPASS_QUERY.format(
        timeout=timeout,
        tight=",".join(str(value) for value in tight),
        wide=",".join(str(value) for value in wide),
    )
    print(f"Overpass: all streets in {tight}")
    print(f"          main roads and water in {wide}")
    last = None

    for url in mirrors:
        request = urllib.request.Request(
            url,
            data=query.encode("utf-8"),
            headers={
                "User-Agent": "siltproof-hackathon/1.0",
                "Content-Type": "text/plain",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout + 15) as response:
                payload = json.loads(response.read().decode("utf-8"))
            print(f"            answered by {url}")
            return payload
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            print(f"            {url} -> {exc}")
            last = exc

    raise last if last else RuntimeError("no Overpass mirror answered")


def features_from_overpass(payload, origin):
    """Overpass elements -> simplified road and water features."""
    features = []
    skipped = 0

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
        highway = tags.get("highway")

        if highway in MAJOR_ROADS:
            kind, road_class = "road", "major"
        elif highway in MINOR_ROADS:
            kind, road_class = "road", "minor"
        elif tags.get("natural") == "water" or tags.get("landuse") in ("reservoir", "basin"):
            kind, road_class = "water", "body"
        elif tags.get("waterway") in ("river", "canal"):
            kind, road_class = "water", "line"
        else:
            continue

        if kind == "road" and length_m(line) < MIN_LENGTH_M:
            skipped += 1
            continue

        thinned = simplify(line, origin)
        closed = thinned[0] == thinned[-1] and len(thinned) > 3

        if kind == "water" and road_class == "body" and closed:
            features.append(
                {
                    "type": "Feature",
                    "properties": {"kind": "water", "class": "body"},
                    "geometry": {"type": "Polygon", "coordinates": [thinned]},
                }
            )
        else:
            features.append(
                {
                    "type": "Feature",
                    "properties": {"kind": kind, "class": road_class},
                    "geometry": {"type": "LineString", "coordinates": thinned},
                }
            )

    if skipped:
        print(f"            dropped {skipped} road stubs under {MIN_LENGTH_M:.0f} m")

    return features


def synthetic_features(bbox, origin, seed="siltproof"):
    """A plausible street grid, for when Overpass is unreachable.

    Clearly flagged in the file's properties so nobody mistakes it for the
    real ward.
    """
    random = ds.rng(seed, "basemap")
    south, west, north, east = bbox
    features = []

    width_m = ds.geo.haversine_m([west, south], [east, south])
    height_m = ds.geo.haversine_m([west, south], [west, north])
    corner = [west, south]

    spacing = 320.0
    for index in range(1, int(width_m // spacing) + 1):
        x = index * spacing + random.uniform(-40, 40)
        line = [
            ds.geo.from_local_m((x, y), corner)
            for y in (0, height_m / 2 + random.uniform(-60, 60), height_m)
        ]
        features.append(
            {
                "type": "Feature",
                "properties": {"kind": "road", "class": "minor" if index % 3 else "major"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[round(lon, 6), round(lat, 6)] for lon, lat in line],
                },
            }
        )

    for index in range(1, int(height_m // spacing) + 1):
        y = index * spacing + random.uniform(-40, 40)
        line = [
            ds.geo.from_local_m((x, y), corner)
            for x in (0, width_m / 2 + random.uniform(-60, 60), width_m)
        ]
        features.append(
            {
                "type": "Feature",
                "properties": {"kind": "road", "class": "minor" if index % 4 else "major"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[round(lon, 6), round(lat, 6)] for lon, lat in line],
                },
            }
        )

    return features


def bbox_of(paths, margin_m):
    """A box around every polygon in these GeoJSON files, plus a margin."""
    lons, lats = [], []

    for path in paths:
        if not pathlib.Path(path).exists():
            continue
        for feature in ds.load_json(path)["features"]:
            for ring in feature["geometry"]["coordinates"]:
                for lon, lat in ring:
                    lons.append(lon)
                    lats.append(lat)

    if not lons:
        raise ValueError(f"no geometry in {paths}")

    dlat = margin_m / ds.geo.M_PER_DEG_LAT
    dlon = margin_m / ds.geo.m_per_deg_lon(sum(lats) / len(lats))

    return (
        round(min(lats) - dlat, 6), round(min(lons) - dlon, 6),
        round(max(lats) + dlat, 6), round(max(lons) + dlon, 6),
    )


def bbox_from_center(center, radius_m):
    lon, lat = center
    dlat = radius_m / ds.geo.M_PER_DEG_LAT
    dlon = radius_m / ds.geo.m_per_deg_lon(lat)
    return (
        round(lat - dlat, 6), round(lon - dlon, 6),
        round(lat + dlat, 6), round(lon + dlon, 6),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--drains", default=None,
                        help="take the bbox from this GeoJSON (default data/out/drains.geojson)")
    parser.add_argument("--center", help="'lat,lon' instead of a drains file")
    parser.add_argument("--radius", type=float, default=2500.0)
    parser.add_argument("--bbox", help="'south,west,north,east'")
    parser.add_argument("--dumpsite", default=None,
                        help="dumpsite.geojson, so the corridor to it is not blank")
    parser.add_argument("--margin", type=float, default=DEFAULT_MARGIN_M,
                        help="metres of context around the drains (default 700)")
    parser.add_argument("--synthetic", action="store_true",
                        help="invent a street grid instead of querying Overpass")
    parser.add_argument("--overpass-json", help="read a saved Overpass response")
    parser.add_argument("--out", default=None)
    parser.add_argument("--seed", default="siltproof")
    args = parser.parse_args(argv)

    wide = None

    if args.bbox:
        bbox = tuple(float(value) for value in args.bbox.split(","))
    elif args.center:
        bbox = bbox_from_center(ds.parse_lat_lon(args.center, "--center"), args.radius)
    else:
        drains = pathlib.Path(args.drains or (ds.OUT / "drains.geojson"))
        if not drains.exists():
            print(f"No drains file at {ds.relative(drains)}.")
            print("Run data/osm_drains.py first, or pass --center / --bbox.")
            return 2
        bbox = bbox_of([drains], args.margin)
        # The dump site is kilometres away and the map fits to both, so the
        # wider box keeps the corridor between them from being blank.
        dumpsite = pathlib.Path(args.dumpsite or (ds.OUT / "dumpsite.geojson"))
        wide = bbox_of([drains, dumpsite], args.margin)

    wide = wide or bbox
    south, west, north, east = bbox
    origin = [(west + east) / 2, (south + north) / 2]

    ds.banner("SiltProof offline basemap")
    print(f"bbox        {bbox}")
    print(f"mode        {'synthetic grid' if args.synthetic else 'OpenStreetMap via Overpass'}")

    if args.synthetic:
        features = synthetic_features(wide, origin, args.seed)
        source = "synthetic"
    else:
        try:
            payload = (
                ds.load_json(args.overpass_json)
                if args.overpass_json
                else fetch_overpass(bbox, wide)
            )
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            print(f"\nOverpass failed: {exc}")
            print("Falling back to a synthetic street grid so the map still has context.")
            print("Re-run without --synthetic when the network is back.")
            features = synthetic_features(wide, origin, args.seed)
            source = "synthetic"
        else:
            features = features_from_overpass(payload, origin)
            source = "openstreetmap"

    roads = sum(1 for feature in features if feature["properties"]["kind"] == "road")
    water = len(features) - roads

    collection = {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "bbox": list(wide),
            "detailBbox": list(bbox),
            "source": source,
            "attribution": (
                "Roads and water: © OpenStreetMap contributors, ODbL"
                if source == "openstreetmap"
                else "Synthetic street grid - not real roads"
            ),
            "note": "Backdrop only, for the offline map style. Not evidence.",
        },
    }

    out = pathlib.Path(args.out or OUT)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Written compactly: it is generated, nobody reads it, and the browser
    # downloads every byte.
    out.write_text(json.dumps(collection, separators=(",", ":")) + "\n")
    size_kb = out.stat().st_size / 1024

    print(f"\nwrote       {ds.relative(out)}  ({roads} roads, {water} water, {size_kb:.0f} KB)")
    if source == "synthetic":
        print("            marked synthetic - say so if it appears in the video")
    else:
        print("            © OpenStreetMap contributors, ODbL")

    return 0


if __name__ == "__main__":
    sys.exit(main())
