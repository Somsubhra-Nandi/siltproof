#!/usr/bin/env python3
"""Generate the bill's dumper trips, GPS traces and slip field values.

    python data/gen_trips.py                 # offline, interpolated traces
    python data/gen_trips.py --live          # routes via Amazon Location

Offline mode interpolates a plausible route with jitter so the whole pipeline
is testable with no AWS account. --live calls Amazon Location's
CalculateRoutes once per drain (not per trip, since every trip from one drain
follows the same road), prints the call plan first, and costs money.

Writes:
    data/out/trips.json                      every trip, with its slip values
    data/out/ground_truth.json               expected verdict + rule ids per trip
    data/out/evidence/traces/B1/<d>-<n>.json the GPS traces, ready to upload
    data/out/mock_manifest.json              merged in by gen_slips/gen_photos

Trace file format (what the ingest Lambda parses):
    {
      "tripId": "14#003", "drainId": "14", "tripNo": "003",
      "vehicleNo": "MH 01 AB 1234",
      "points": [{"t": "<iso8601>", "lat": 19.07, "lon": 72.87}, ...]
    }
One point every 30 s, in order. The planted cases deliberately break it: a
trace that never reaches the dump site, and one with a four-minute hole.
"""

import argparse
import datetime
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import dataset as ds  # noqa: E402

# Urban dumper speed, used to time the trace points.
SPEED_KMH = 24.0
DWELL_AT_DUMP_S = 240       # tipping time, so the trace sits inside the geofence
AVG_LOAD_T = 10.5           # drives how many trips a drain's tonnage needs

WORK_DAYS = 10              # trips are spread over this many days in the window
FIRST_TRIP_HOUR = 7
TRIPS_PER_DAY_PER_DRAIN = 3

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

# Forced trips, so the planted cases land on exact tonnages and the bill's
# totals come out at the plan's headline numbers (see DECISIONS.md).
#   drain -> list of (load tonnes, marker)
FORCED_TRIPS = {
    "11": [(14.0, "R7_OVERLOAD"), (14.0, "R7_OVERLOAD")],
    "16": [(12.0, "R6_IMPOSSIBLE"), (12.0, "R6_IMPOSSIBLE")],
    "6": [(10.0, "GPS_GAP")],
}

OVERLOAD_CAPACITY = 10      # the truck the drain 11 slips overload
GPS_GAP_S = 240             # the honest four-minute gap in an underpass
R6_GAP_MINUTES = 7          # drain 16: second trip starts this soon after
R6_DISTANCE_KM = 22         # ...and this far away
R8_SLIP_EARLY_MIN = 40      # drain 14: slip printed before the truck arrives
DETOUR_SHORTFALL_M = 2200   # drain 14 stops this far short of the dump site


def iso(stamp):
    return stamp.isoformat()


# ------------------------------------------------------------------ geometry
def polygon_centroid(ring):
    """Area centroid of a GeoJSON ring, good enough for a loading point."""
    points = ring[:-1] if ring[0] == ring[-1] else ring
    area = 0.0
    cx = cy = 0.0

    for index in range(len(points)):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % len(points)]
        cross = x1 * y2 - x2 * y1
        area += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross

    if abs(area) < 1e-12:
        return [
            sum(p[0] for p in points) / len(points),
            sum(p[1] for p in points) / len(points),
        ]

    area *= 0.5
    return [cx / (6 * area), cy / (6 * area)]


def interpolated_route(origin, destination, random):
    """A plausible road-ish path: a few bent waypoints, not a straight line."""
    distance = ds.geo.haversine_m(origin, destination)
    legs = max(3, min(8, int(distance / 900)))

    path = [origin]
    for index in range(1, legs):
        fraction = index / legs
        straight = ds.geo.interpolate(origin, destination, fraction)
        # Push the waypoint sideways so the track bends like a street grid.
        sideways = math.sin(fraction * math.pi) * random.uniform(-260, 260)
        bearing = math.atan2(destination[1] - origin[1], destination[0] - origin[0])
        path.append(
            ds.geo.offset_m(
                straight,
                math.cos(bearing + math.pi / 2) * sideways,
                math.sin(bearing + math.pi / 2) * sideways,
            )
        )
    path.append(destination)
    return path


def densify(path, start_time, random, interval_s=ds.TRACE_INTERVAL_S, jitter_m=6.0):
    """Walk a path at SPEED_KMH, emitting one point every interval_s."""
    speed_ms = SPEED_KMH * 1000 / 3600
    step_m = speed_ms * interval_s

    points = []
    elapsed = 0.0
    carry = 0.0

    for index in range(len(path) - 1):
        a, b = path[index], path[index + 1]
        leg_m = ds.geo.haversine_m(a, b)
        travelled = carry

        while travelled <= leg_m:
            fraction = travelled / leg_m if leg_m else 0.0
            point = ds.geo.interpolate(a, b, fraction)
            point = ds.geo.offset_m(
                point, random.uniform(-jitter_m, jitter_m), random.uniform(-jitter_m, jitter_m)
            )
            points.append(
                {
                    "t": iso(start_time + datetime.timedelta(seconds=round(elapsed))),
                    "lat": round(point[1], 6),
                    "lon": round(point[0], 6),
                }
            )
            travelled += step_m
            elapsed += interval_s

        carry = travelled - leg_m

    return points


def dwell(points, center, seconds, random):
    """Add stationary points at the destination, so the trace enters it."""
    if not points:
        return points

    last = datetime.datetime.fromisoformat(points[-1]["t"])
    for step in range(1, int(seconds / ds.TRACE_INTERVAL_S) + 1):
        jittered = ds.geo.offset_m(center, random.uniform(-25, 25), random.uniform(-25, 25))
        points.append(
            {
                "t": iso(last + datetime.timedelta(seconds=step * ds.TRACE_INTERVAL_S)),
                "lat": round(jittered[1], 6),
                "lon": round(jittered[0], 6),
            }
        )
    return points


# -------------------------------------------------------------------- loads
def plan_loads(target_tonnes, drain_id, random):
    """Trip loads for one drain, summing to exactly its claimed tonnage."""
    forced = [load for load, _ in FORCED_TRIPS.get(drain_id, [])]
    markers = [marker for _, marker in FORCED_TRIPS.get(drain_id, [])]

    remaining = round(target_tonnes - sum(forced), 2)
    if remaining < 0:
        raise ValueError(f"drain {drain_id}: forced trips exceed its claimed tonnage")

    count = max(1, round(remaining / AVG_LOAD_T)) if remaining > 0 else 0
    loads = []

    if count:
        base = remaining / count
        for _ in range(count - 1):
            loads.append(round(base + random.uniform(-1.6, 1.6), 1))
        loads.append(round(remaining - sum(loads), 1))

        # Keep the balancing trip inside a believable range.
        while loads[-1] > 15.5 and len(loads) > 1:
            loads[-2] = round(loads[-2] + 1.0, 1)
            loads[-1] = round(loads[-1] - 1.0, 1)
        while loads[-1] < 5.0 and len(loads) > 1:
            loads[-2] = round(loads[-2] - 1.0, 1)
            loads[-1] = round(loads[-1] + 1.0, 1)

    plan = [(load, None) for load in loads] + list(zip(forced, markers))
    random.shuffle(plan)
    return plan


def pick_vehicle(fleet, load, marker, random):
    if marker == "R7_OVERLOAD":
        small = [v for v in fleet if v["capacityTonnes"] == OVERLOAD_CAPACITY]
        return random.choice(small or fleet)

    able = [v for v in fleet if v["capacityTonnes"] >= load]
    return random.choice(able or fleet)


def tare_for(capacity):
    return {10: 8.4, 14: 11.2, 16: 12.6}.get(capacity, 11.0)


# -------------------------------------------------------------------- trips
def schedule_start(drain_index, trip_index, random):
    day = (drain_index + trip_index // TRIPS_PER_DAY_PER_DRAIN) % WORK_DAYS
    slot = trip_index % TRIPS_PER_DAY_PER_DRAIN

    window_start = datetime.datetime.fromisoformat(ds.WORK_WINDOW[0])
    base = window_start.replace(hour=FIRST_TRIP_HOUR, minute=0, second=0, microsecond=0)

    return base + datetime.timedelta(
        days=day,
        minutes=slot * 165 + random.randint(0, 25),
    )


def build_trip(*, drain, drain_index, trip_index, load, marker, fleet, route, dumpsite_center, random):
    drain_id = drain["drainId"]
    trip_no = f"{trip_index + 1:03d}"
    vehicle = pick_vehicle(fleet, load, marker, random)

    start = schedule_start(drain_index, trip_index, random)
    is_detour = drain_id == "14"

    path = list(route)
    if is_detour:
        # Stop short of the dump site, at a vacant plot off the route.
        total = ds.geo.haversine_m(path[0], path[-1])
        fraction = max(0.2, (total - DETOUR_SHORTFALL_M) / total) if total else 0.5
        plot = ds.geo.interpolate(path[0], path[-1], fraction)
        plot = ds.geo.offset_m(plot, 420, -380)
        path = path[:-1] + [plot]

    points = densify(path, start, random)
    end_center = [points[-1]["lon"], points[-1]["lat"]] if points else path[-1]
    points = dwell(points, end_center if is_detour else dumpsite_center, DWELL_AT_DUMP_S, random)

    gap_seconds = 0
    if marker == "GPS_GAP" and len(points) > 10:
        # Drop the points covering four minutes in the middle: an underpass.
        drop_from = len(points) // 2
        drop_count = max(1, GPS_GAP_S // ds.TRACE_INTERVAL_S)
        points = points[:drop_from] + points[drop_from + drop_count:]
        gap_seconds = GPS_GAP_S + ds.TRACE_INTERVAL_S

    arrival = datetime.datetime.fromisoformat(points[-1]["t"]) - datetime.timedelta(
        seconds=DWELL_AT_DUMP_S
    )

    slip_time_in = arrival + datetime.timedelta(minutes=random.randint(-2, 3))
    if is_detour:
        slip_time_in = arrival - datetime.timedelta(minutes=R8_SLIP_EARLY_MIN)

    capacity = vehicle["capacityTonnes"]
    tare = tare_for(capacity)

    return {
        "tripId": ds.trip_id(drain_id, trip_no),
        "drainId": drain_id,
        "tripNo": trip_no,
        "vehicleNo": vehicle["vehicleNo"],
        "capacityTonnes": capacity,
        "claimedTonnes": load,
        "marker": marker,
        "startTime": iso(start),
        "arrivalTime": iso(arrival),
        "endTime": points[-1]["t"],
        "enteredDumpsite": not is_detour,
        "maxGapSeconds": gap_seconds or ds.TRACE_INTERVAL_S,
        "routeDistanceM": round(
            sum(
                ds.geo.haversine_m(
                    [points[i]["lon"], points[i]["lat"]], [points[i + 1]["lon"], points[i + 1]["lat"]]
                )
                for i in range(len(points) - 1)
            ),
            1,
        ),
        "traceKey": ds.trace_key(drain_id, trip_no),
        "slipKey": ds.slip_key(drain_id, trip_no),
        "slip": {
            "ticketNo": f"WB-2026-{random.randint(10000, 99999)}",
            "vehicleNo": vehicle["vehicleNo"],
            "gross": round(tare + load, 2),
            "tare": tare,
            "net": round(load, 2),
            "timeIn": slip_time_in.strftime("%H:%M"),
            "timeOut": (slip_time_in + datetime.timedelta(minutes=random.randint(14, 26))).strftime("%H:%M"),
            "site": "Ward storm water drain desilting",
            "date": slip_time_in.strftime("%d/%m/%Y"),
        },
        "_points": points,
    }


def apply_r6_case(trips, dumpsite_center):
    """Drain 16: the same truck logs two trips 7 min apart and 22 km apart."""
    flagged = [trip for trip in trips if trip["marker"] == "R6_IMPOSSIBLE"]
    if len(flagged) < 2:
        return

    first, second = flagged[0], flagged[1]
    second["vehicleNo"] = first["vehicleNo"]
    second["capacityTonnes"] = first["capacityTonnes"]
    second["slip"]["vehicleNo"] = first["vehicleNo"]

    first_end = datetime.datetime.fromisoformat(first["endTime"])
    new_start = first_end + datetime.timedelta(minutes=R6_GAP_MINUTES)

    # Move the second trip's whole trace so that it starts exactly
    # R6_DISTANCE_KM from where the first trip ended, and restamp it: the truck
    # would have to average far over 40 km/h to be in both places.
    first_last = first["_points"][-1]
    target_start = ds.geo.offset_m(
        [first_last["lon"], first_last["lat"]], R6_DISTANCE_KM * 1000, 0
    )
    shift_lon = target_start[0] - second["_points"][0]["lon"]
    shift_lat = target_start[1] - second["_points"][0]["lat"]

    original_start = datetime.datetime.fromisoformat(second["_points"][0]["t"])
    for point in second["_points"]:
        stamp = datetime.datetime.fromisoformat(point["t"])
        point["t"] = iso(new_start + (stamp - original_start))
        point["lon"] = round(point["lon"] + shift_lon, 6)
        point["lat"] = round(point["lat"] + shift_lat, 6)

    second["startTime"] = second["_points"][0]["t"]
    second["endTime"] = second["_points"][-1]["t"]
    arrival = datetime.datetime.fromisoformat(second["endTime"]) - datetime.timedelta(
        seconds=DWELL_AT_DUMP_S
    )
    second["arrivalTime"] = iso(arrival)
    second["slip"]["timeIn"] = arrival.strftime("%H:%M")
    second["slip"]["timeOut"] = (arrival + datetime.timedelta(minutes=18)).strftime("%H:%M")
    second["r6PairedWith"] = first["tripId"]
    # The truck never got near the approved dump site on this trip either.
    second["enteredDumpsite"] = False


# ------------------------------------------------------------ ground truth
def expected_for(trip, drain_id):
    """Expected verdict and rule ids for one trip (plan sections 5 and 6)."""
    planted = ds.PLANTED.get(drain_id)
    hard, soft = [], []

    if planted:
        scope = planted["scope"]
        if scope == "hold_all":
            hard.extend(planted["photoRules"])
            if drain_id == "14":
                hard.extend(planted["tripRules"])
        elif scope == "review_all":
            soft.extend(planted["photoRules"])
        else:
            marker = trip["marker"]
            if marker == "R7_OVERLOAD":
                hard.append("R7")
            elif marker == "R6_IMPOSSIBLE":
                hard.append("R6")
                if not trip["enteredDumpsite"]:
                    hard.append("R5")
            elif marker == "GPS_GAP":
                soft.append("GPS_GAP")

    verdict = "HOLD" if hard else ("REVIEW" if soft else "VERIFIED")
    return verdict, sorted(set(hard)), sorted(set(soft))


def build_ground_truth(trips, drains):
    rows = []
    for trip in trips:
        verdict, hard, soft = expected_for(trip, trip["drainId"])
        trip["expectedVerdict"] = verdict
        trip["expectedHardFails"] = hard
        trip["expectedSoftFails"] = soft
        rows.append(
            {
                "tripId": trip["tripId"],
                "drainId": trip["drainId"],
                "tripNo": trip["tripNo"],
                "vehicleNo": trip["vehicleNo"],
                "capacityTonnes": trip["capacityTonnes"],
                "claimedTonnes": trip["claimedTonnes"],
                "expectedVerdict": verdict,
                "hardFails": hard,
                "softFails": soft,
            }
        )

    def tonnes(verdict, drain_id=None):
        return round(
            sum(
                row["claimedTonnes"]
                for row in rows
                if row["expectedVerdict"] == verdict
                and (drain_id is None or row["drainId"] == drain_id)
            ),
            1,
        )

    drain_rows = {}
    for drain in drains:
        drain_id = drain["drainId"]
        held = tonnes("HOLD", drain_id)
        review = tonnes("REVIEW", drain_id)
        colour = "RED" if held else ("AMBER" if review else "GREEN")
        planted = ds.PLANTED.get(drain_id)
        drain_rows[drain_id] = {
            "claimedTonnes": ds.DRAIN_CLAIMED[drain_id],
            "verifiedTonnes": tonnes("VERIFIED", drain_id),
            "reviewTonnes": review,
            "heldTonnes": held,
            "expectedColour": colour,
            "story": planted["story"] if planted else "Clean",
        }

    verified, review, held = tonnes("VERIFIED"), tonnes("REVIEW"), tonnes("HOLD")

    return {
        "billId": ds.BILL_ID,
        "ratePerTonne": ds.RATE_PER_TONNE,
        "note": "Expected output of rules R1-R10 for the generated dataset. "
                "Day 2 tests the api Lambda against this file.",
        "totals": {
            "tripCount": len(rows),
            "claimedTonnes": round(verified + review + held, 1),
            "verifiedTonnes": verified,
            "reviewTonnes": review,
            "heldTonnes": held,
            "heldRupees": ds.rupees(held),
            "verifiedAfterApprovals": round(verified + review, 1),
        },
        "drains": drain_rows,
        "trips": rows,
    }


def check_targets(ground_truth):
    """Fail loudly if the dataset drifted off the plan's headline numbers."""
    totals = ground_truth["totals"]
    problems = []

    for name, target in (
        ("claimedTonnes", ds.TARGETS["claimedTonnes"]),
        ("heldTonnes", ds.TARGETS["heldTonnes"]),
        ("reviewTonnes", ds.TARGETS["reviewTonnes"]),
        ("verifiedTonnes", ds.TARGETS["verifiedTonnes"]),
        ("verifiedAfterApprovals", ds.TARGETS["verifiedAfterApprovals"]),
        ("heldRupees", ds.TARGETS["heldRupees"]),
    ):
        actual = totals[name]
        if abs(actual - target) > 0.51:
            problems.append(f"{name}: {actual} != {target}")

    expected_colours = {
        drain_id: planted["colour"] for drain_id, planted in ds.PLANTED.items()
    }
    for drain_id, colour in expected_colours.items():
        actual = ground_truth["drains"][drain_id]["expectedColour"]
        if actual != colour:
            problems.append(f"drain {drain_id} colour: {actual} != {colour}")

    for drain_id, row in ground_truth["drains"].items():
        if drain_id in ds.PLANTED:
            continue
        if row["expectedColour"] != "GREEN":
            problems.append(f"drain {drain_id} should be clean, got {row['expectedColour']}")

    return problems


# --------------------------------------------------------------------- main
def route_for_drain(drain, dumpsite_center, live, random, calls):
    origin = polygon_centroid(drain["ring"])

    if not live:
        return interpolated_route(origin, dumpsite_center, random)

    from common import location

    calls["location"] += 1
    response = location.calculate_route(origin, dumpsite_center)
    parsed = location.parse_route(response)

    if not parsed["ok"]:
        print(f"  drain {drain['drainId']}: no route returned, interpolating instead")
        return interpolated_route(origin, dumpsite_center, random)

    return parsed["points"]


def load_drains(path):
    collection = ds.load_json(path)
    drains = []
    for feature in collection["features"]:
        properties = feature["properties"]
        drains.append(
            {
                "drainId": properties["drainId"],
                "name": properties["name"],
                "ring": feature["geometry"]["coordinates"][0],
                "properties": properties,
            }
        )
    drains.sort(key=lambda drain: int(drain["drainId"]))
    return drains, collection.get("properties", {})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--drains", default=None, help="drains.geojson (default data/out/drains.geojson)")
    parser.add_argument("--dumpsite", default=None, help="dumpsite.geojson (default data/out/dumpsite.geojson)")
    parser.add_argument("--series", default="MH", help="vehicle plate series (default MH)")
    parser.add_argument("--seed", default="siltproof")
    parser.add_argument(
        "--live",
        action="store_true",
        help="route with Amazon Location instead of interpolating. Costs money.",
    )
    parser.add_argument("--yes", action="store_true", help="skip the --live confirmation")
    args = parser.parse_args(argv)

    drains_path = args.drains or (ds.OUT / "drains.geojson")
    dumpsite_path = args.dumpsite or (ds.OUT / "dumpsite.geojson")

    if not pathlib.Path(drains_path).exists():
        print(f"No drains file at {ds.relative(drains_path)}.")
        print("Run data/osm_drains.py first (use --synthetic to work offline).")
        return 2

    drains, _ = load_drains(drains_path)
    dumpsite = ds.load_json(dumpsite_path)
    dumpsite_center = dumpsite["features"][0]["properties"]["center"]

    ds.banner("SiltProof trips and traces")
    print(f"drains      {len(drains)} from {ds.relative(drains_path)}")
    print(f"dump site   {dumpsite_center[1]:.5f}, {dumpsite_center[0]:.5f}")
    print(f"mode        {'Amazon Location (live, billable)' if args.live else 'offline interpolation'}")

    if args.live and not args.yes:
        print(f"\nWould call Amazon Location CalculateRoutes {len(drains)} times")
        print("(one route per drain, reused by every trip from that drain).")
        reply = input("Proceed? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Nothing called.")
            return 1

    fleet = ds.vehicles(args.series)
    calls = {"location": 0}
    trips = []

    for drain_index, drain in enumerate(drains):
        drain_id = drain["drainId"]
        random = ds.rng(args.seed, f"drain-{drain_id}")
        route = route_for_drain(drain, dumpsite_center, args.live, random, calls)

        plan = plan_loads(ds.DRAIN_CLAIMED[drain_id], drain_id, random)
        drain_trips = []

        for trip_index, (load, marker) in enumerate(plan):
            drain_trips.append(
                build_trip(
                    drain=drain,
                    drain_index=drain_index,
                    trip_index=trip_index,
                    load=load,
                    marker=marker,
                    fleet=fleet,
                    route=route,
                    dumpsite_center=dumpsite_center,
                    random=random,
                )
            )

        if drain_id == "16":
            apply_r6_case(drain_trips, dumpsite_center)

        trips.extend(drain_trips)

    # ---- write the traces, then strip the points out of trips.json
    trace_dir = ds.OUT / "evidence"
    written = 0
    for trip in trips:
        points = trip.pop("_points")
        path = trace_dir / trip["traceKey"]
        ds.save_json(
            path,
            {
                "tripId": trip["tripId"],
                "drainId": trip["drainId"],
                "tripNo": trip["tripNo"],
                "vehicleNo": trip["vehicleNo"],
                "points": points,
            },
        )
        trip["pointCount"] = len(points)
        written += 1

    ground_truth = build_ground_truth(trips, drains)

    ds.save_json(ds.OUT / "trips.json", {"billId": ds.BILL_ID, "trips": trips})
    ds.save_json(ds.OUT / "ground_truth.json", ground_truth)

    # Slip values go into the mock manifest, so a mocked Textract call returns
    # what the slip image actually says.
    manifest_path = ds.OUT / "mock_manifest.json"
    manifest = ds.load_json(manifest_path) if manifest_path.exists() else {}
    manifest.setdefault("slips", {})
    for trip in trips:
        manifest["slips"][trip["slipKey"]] = dict(trip["slip"])
    ds.save_json(manifest_path, manifest)

    totals = ground_truth["totals"]
    print(f"\ntrips       {totals['tripCount']}")
    print(f"traces      {written} written under {ds.relative(trace_dir)}/traces")
    print(f"claimed     {totals['claimedTonnes']} t  ({ds.lakh(ds.rupees(totals['claimedTonnes']))})")
    print(f"verified    {totals['verifiedTonnes']} t")
    print(f"review      {totals['reviewTonnes']} t")
    print(f"hold        {totals['heldTonnes']} t  ({ds.lakh(totals['heldRupees'])})")
    print(f"after the engineer approves the review drains: {totals['verifiedAfterApprovals']} t verified")

    if args.live:
        print(f"\nbillable    {calls['location']} Amazon Location route calculations")

    problems = check_targets(ground_truth)
    if problems:
        print("\nTargets missed:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("\nAll plan section 6 targets hit, and every planted case is in place.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
