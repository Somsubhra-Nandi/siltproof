"""Small geodesy helpers, pure Python so they work in Lambda and in scripts.

Everything is [lon, lat] to match GeoJSON. Over one ward a local flat
approximation is accurate to centimetres, which is far below GPS noise.
"""

import math

# Metres per degree of latitude (close enough anywhere in India).
M_PER_DEG_LAT = 111_320.0

EARTH_RADIUS_M = 6_371_000.0


def m_per_deg_lon(lat):
    return M_PER_DEG_LAT * math.cos(math.radians(lat))


def haversine_m(a, b):
    """Great-circle distance in metres between two [lon, lat] points."""
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])

    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def offset_m(point, east_m, north_m):
    """Move a [lon, lat] point by a local metre offset."""
    lon, lat = point
    return [lon + east_m / m_per_deg_lon(lat), lat + north_m / M_PER_DEG_LAT]


def to_local_m(point, origin):
    """Project [lon, lat] to local metres (east, north) around an origin."""
    return (
        (point[0] - origin[0]) * m_per_deg_lon(origin[1]),
        (point[1] - origin[1]) * M_PER_DEG_LAT,
    )


def from_local_m(xy, origin):
    """Inverse of to_local_m."""
    return [
        origin[0] + xy[0] / m_per_deg_lon(origin[1]),
        origin[1] + xy[1] / M_PER_DEG_LAT,
    ]


def interpolate(a, b, fraction):
    return [a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction]


def point_in_ring(point, ring):
    """Ray casting. ring is a list of [lon, lat], closed or not."""
    lon, lat = point[0], point[1]
    inside = False
    count = len(ring)

    for index in range(count):
        x1, y1 = ring[index][0], ring[index][1]
        x2, y2 = ring[(index + 1) % count][0], ring[(index + 1) % count][1]

        if (y1 > lat) != (y2 > lat):
            x_at = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x_at:
                inside = not inside

    return inside


def point_in_polygon(point, polygon):
    """polygon is GeoJSON Polygon coordinates: [outer_ring, hole, ...]."""
    if not polygon:
        return False
    if not point_in_ring(point, polygon[0]):
        return False
    return not any(point_in_ring(point, hole) for hole in polygon[1:])


def point_in_geometry(point, geometry):
    """Accepts a GeoJSON Polygon or MultiPolygon geometry dict."""
    if not isinstance(geometry, dict):
        return False

    kind = geometry.get("type")
    coordinates = geometry.get("coordinates") or []

    if kind == "Polygon":
        return point_in_polygon(point, coordinates)
    if kind == "MultiPolygon":
        return any(point_in_polygon(point, polygon) for polygon in coordinates)
    return False


def distance_to_segment_m(point, a, b):
    """Shortest distance in metres from a point to the segment a-b."""
    px, py = to_local_m(point, a)
    bx, by = to_local_m(b, a)

    length_sq = bx * bx + by * by
    if length_sq == 0:
        return math.hypot(px, py)

    t = max(0.0, min(1.0, (px * bx + py * by) / length_sq))
    return math.hypot(px - t * bx, py - t * by)


def distance_to_line_m(point, line):
    """Shortest distance in metres from a point to a polyline."""
    if not line:
        return float("inf")
    if len(line) == 1:
        return haversine_m(point, line[0])
    return min(
        distance_to_segment_m(point, line[index], line[index + 1])
        for index in range(len(line) - 1)
    )


def bbox_of(points):
    """[minLon, minLat, maxLon, maxLat] for a list of [lon, lat]."""
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    return [min(lons), min(lats), max(lons), max(lats)]
