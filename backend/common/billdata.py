"""Assemble a bill's state out of DynamoDB and S3 for the rules to judge.

Kept apart from both the rules (which are pure) and the API (which is HTTP
plumbing), so the expensive part - reading a hundred-odd traces - has one home
and one set of tests.
"""

import concurrent.futures
import json

from . import awsclients, config, geo, store
from .jsonlog import log

# Traces are small JSON files; fetching them in parallel keeps a verification
# inside the HTTP API's 30 s ceiling.
TRACE_FETCH_WORKERS = 16

# How many points of a trace the drill-down map needs. The browser cannot tell
# 40 points from 400 at ward zoom.
ROUTE_MAX_POINTS = 60


def _trace_points(payload):
    if isinstance(payload, dict):
        points = payload.get("points") or []
    elif isinstance(payload, list):
        points = payload
    else:
        points = []

    cleaned = []
    for point in points:
        if isinstance(point, dict) and point.get("lat") is not None:
            cleaned.append(
                {"lat": float(point["lat"]), "lon": float(point["lon"]), "t": point.get("t")}
            )
    return cleaned


def load_traces(bucket, keys):
    """{key: [points]} for every trace that could be read.

    A key that is missing or unreadable is simply absent from the result; the
    rules turn that into an explicit "no GPS trace" finding rather than a
    silent pass.
    """
    client = awsclients.client("s3")
    traces = {}
    problems = {}

    def fetch(key):
        try:
            body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
            return key, _trace_points(json.loads(body.decode("utf-8"))), None
        except Exception as exc:
            return key, None, f"{type(exc).__name__}: {exc}"

    keys = [key for key in keys if key]
    if not keys:
        return traces, problems

    with concurrent.futures.ThreadPoolExecutor(max_workers=TRACE_FETCH_WORKERS) as pool:
        for key, points, error in pool.map(fetch, keys):
            if error:
                problems[key] = error
            else:
                traces[key] = points

    if problems:
        log("trace_fetch_problems", count=len(problems), sample=list(problems)[:3])

    return traces, problems


def load_context(bill_id=None, *, bucket=None, with_traces=True):
    """Everything the rules need for one bill."""
    bill_id = bill_id or config.bill_id()
    bucket = bucket or config.evidence_bucket()
    partition = store.bill_pk(bill_id)

    rows = store.query_pk(partition)
    bill = next((row for row in rows if row["sk"] == "META"), None)
    drains = [row for row in rows if row["sk"].startswith("DRAIN#")]
    trips = [row for row in rows if row["sk"].startswith("TRIP#")]

    drains.sort(key=lambda drain: int(drain.get("drainId") or 0))

    photos = store.scan_sk("PHOTO", billId=bill_id)

    slip_keys = [trip.get("slipKey") for trip in trips if trip.get("slipKey")]
    trace_keys = [trip.get("traceKey") for trip in trips if trip.get("traceKey")]

    evidence = store.batch_get(
        [(store.evidence_pk(key), "SLIP") for key in slip_keys]
        + [(store.evidence_pk(key), "TRACE") for key in trace_keys]
    )
    slips = {
        item["s3Key"]: item
        for (_, sort_key), item in evidence.items()
        if sort_key == "SLIP" and item.get("s3Key")
    }
    trace_items = {
        item["s3Key"]: item
        for (_, sort_key), item in evidence.items()
        if sort_key == "TRACE" and item.get("s3Key")
    }

    vehicle_numbers = {trip.get("vehicleNo") for trip in trips if trip.get("vehicleNo")}
    vehicle_items = store.batch_get(
        [(store.vehicle_pk(number), "META") for number in vehicle_numbers]
    )
    vehicles = {
        item["vehicleNo"]: item.get("capacityTonnes")
        for item in vehicle_items.values()
        if item.get("vehicleNo")
    }

    dumpsite = store.get(store.dumpsite_pk("D1"), "META")

    traces, trace_problems = ({}, {})
    if with_traces:
        traces, trace_problems = load_traces(bucket, trace_keys)

    return {
        "billId": bill_id,
        "bill": bill,
        "drains": drains,
        "trips": trips,
        "photos": photos,
        "slips": slips,
        "traceItems": trace_items,
        "traces": traces,
        "traceProblems": trace_problems,
        "vehicles": vehicles,
        "dumpsite": dumpsite,
    }


def missing_evidence(context):
    """A plain count of what never arrived or failed to be read."""
    trips = context.get("trips") or []
    slips = context.get("slips") or {}
    trace_items = context.get("traceItems") or {}
    photos = context.get("photos") or []

    return {
        "tripsWithoutSlip": sum(1 for trip in trips if trip.get("slipKey") not in slips),
        "tripsWithoutTrace": sum(
            1 for trip in trips if trip.get("traceKey") not in trace_items
        ),
        "unreadableTraces": len(context.get("traceProblems") or {}),
        "evidenceErrors": sum(
            1
            for item in list(photos) + list(slips.values()) + list(trace_items.values())
            if item.get("status") == "ERROR"
        ),
        "drainsWithoutPhotos": sum(
            1
            for drain in context.get("drains") or []
            if not any(photo.get("drainId") == drain.get("drainId") for photo in photos)
        ),
    }


def downsample(points, limit=ROUTE_MAX_POINTS):
    """Thin a trace to at most `limit` points, always keeping both ends."""
    if len(points) <= limit:
        return points
    step = len(points) / (limit - 1)
    thinned = [points[int(index * step)] for index in range(limit - 1)]
    thinned.append(points[-1])
    return thinned


def polygon_centroid(ring):
    points = ring[:-1] if ring and ring[0] == ring[-1] else (ring or [])
    if not points:
        return None

    area = cx = cy = 0.0
    for index in range(len(points)):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % len(points)]
        cross = x1 * y2 - x2 * y1
        area += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross

    if abs(area) < 1e-12:
        return [
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        ]

    area *= 0.5
    return [round(cx / (6 * area), 7), round(cy / (6 * area), 7)]


def claimed_route(drain, dumpsite):
    """What the bill implies the truck did: drain to the approved dump site.

    Drawn against the actual GPS trace in the drill-down, which is the whole
    point of the hero case - the two lines do not meet.
    """
    geofence = (drain or {}).get("geofence") or {}
    rings = geofence.get("coordinates") or []
    origin = polygon_centroid(rings[0]) if rings else None
    destination = (dumpsite or {}).get("center")

    if not origin or not destination:
        return None

    return [origin, [round(destination[0], 7), round(destination[1], 7)]]


def route_distance_m(points):
    if len(points) < 2:
        return 0.0
    return round(
        sum(
            geo.haversine_m(
                [points[index]["lon"], points[index]["lat"]],
                [points[index + 1]["lon"], points[index + 1]["lat"]],
            )
            for index in range(len(points) - 1)
        ),
        1,
    )
