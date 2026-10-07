"""Amazon Location: route calculation for generating dumper traces.

Uses the resource-free ``geo-routes`` API, so there is no route calculator to
create. Map tiles are a separate concern and are fetched directly by the
browser with an API key.
"""

from . import awsclients, config, mocks
from .jsonlog import log
from .retry import call_with_retry


def calculate_route(origin, destination):
    """origin/destination are [lon, lat]. Returns the raw response."""
    if config.mock_aws():
        log("location_mocked", call="calculate_routes")
        return mocks.location_calculate_routes(origin, destination)

    client = awsclients.client("geo-routes")
    return call_with_retry(
        lambda: client.calculate_routes(
            Origin=list(origin),
            Destination=list(destination),
            TravelMode="Car",
            LegGeometryFormat="Simple",
        ),
        what="location.calculate_routes",
    )


def _walk_linestrings(node, found):
    """Collect every Geometry.LineString in the response, in document order.

    Written defensively on purpose: the live shape could not be verified while
    the account was pending activation, so the parser looks for the geometry
    rather than assuming one exact path to it.
    """
    if isinstance(node, dict):
        geometry = node.get("Geometry")
        if isinstance(geometry, dict):
            line = geometry.get("LineString")
            if isinstance(line, list) and line:
                found.append(line)
        for key, value in node.items():
            if key != "Geometry":
                _walk_linestrings(value, found)
    elif isinstance(node, list):
        for item in node:
            _walk_linestrings(item, found)
    return found


def parse_route(response):
    """Raw CalculateRoutes response -> {points: [[lon, lat], ...], distanceM, durationS}."""
    routes = response.get("Routes") or []
    first = routes[0] if routes else {}

    points = []
    for line in _walk_linestrings(first, []):
        for point in line:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            coordinate = [float(point[0]), float(point[1])]
            if not points or points[-1] != coordinate:
                points.append(coordinate)

    summary = first.get("Summary") or {}
    distance = summary.get("Distance")
    duration = summary.get("Duration")

    if distance is None or duration is None:
        # Some shapes carry the numbers one level down, under the leg.
        for leg in first.get("Legs") or []:
            overview = ((leg.get("VehicleLegDetails") or {}).get("Summary") or {}).get(
                "Overview"
            ) or {}
            distance = distance if distance is not None else overview.get("Distance")
            duration = duration if duration is not None else overview.get("Duration")

    return {
        "points": points,
        "distanceM": float(distance) if distance is not None else None,
        "durationS": float(duration) if duration is not None else None,
        "ok": len(points) >= 2,
    }
