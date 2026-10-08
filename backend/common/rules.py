"""The ten verification rules (plan section 5).

Pure functions over plain dicts. Nothing here touches AWS: the api Lambda
assembles a context from DynamoDB and S3 and hands it over, which keeps the
rules fast, deterministic and testable offline against
``data/out/ground_truth.json``.

Two kinds of rule:

* **Photo rules** (R1-R4) and **R10** judge a drain's own evidence, so a
  failure taints every trip billed against that drain. A reused after-photo
  does not discredit one lorry load, it discredits the drain's claim.
* **Trip rules** (R5-R9, plus the GPS-gap flag) judge one lorry load.

Severity follows the plan, with one agreed change: **R1 has a soft band.**
A photo inside the 30 m geofence passes, one between 30 m and 60 m is a soft
fail, and one beyond 60 m is a hard fail. Consumer GPS is routinely tens of
metres out, so "just outside" is a question for the engineer, not proof.
Recorded in DECISIONS.md and reflected in plan section 5.

Missing evidence is never silently ignored and never treated as fraud: it
raises its own soft finding, so the trip goes to the engineer for review.
"""

import datetime

from . import config, geo

# Thresholds, all in one place so the UI can explain them.
SPEED_LIMIT_KMH = 40.0          # R6
SLIP_TOLERANCE_MIN = 10.0       # R8
GPS_GAP_SOFT_S = 180.0          # the extra soft flag in plan section 5
PHASH_DUPLICATE_MAX = 12        # R3; measured, see DECISIONS.md
R10_ALLOWANCE = 1.2             # R10's x1.2 headroom

HARD = "hard"
SOFT = "soft"

HOLD = "HOLD"
REVIEW = "REVIEW"
VERIFIED = "VERIFIED"

RED, AMBER, GREEN = "RED", "AMBER", "GREEN"

# Rule id -> what it checks, in the engineer's words. The UI reads these
# rather than keeping its own copy.
RULE_TEXT = {
    "R1": "Photo GPS must fall inside the drain's 30 m geofence",
    "R2": "Photo must have been taken inside the bill's work window",
    "R3": "Photo must not be a reused copy of another photo on this bill",
    "R4": "After-photo must show a cleared drain, and the load must be silt",
    "R5": "GPS trace must enter the approved dump site",
    "R6": "Consecutive trips by one truck must be physically possible",
    "R7": "Slip net weight must not exceed the truck's capacity",
    "R8": "Slip time-in must match the GPS arrival at the dump site",
    "R9": "Slip vehicle number must match the trip's truck",
    "R10": "Drain total must fit the section's length, width and depth",
    "GPS_GAP": "GPS trace must not go dark for more than three minutes",
    "SLIP_MISSING": "A weighbridge slip must exist for the trip",
    "TRACE_MISSING": "A GPS trace must exist for the trip",
    "PHOTOS_MISSING": "The drain must have photo evidence",
    "EVIDENCE_ERROR": "Evidence must have been read successfully",
}


def finding(rule, severity, message, **evidence):
    return {
        "rule": rule,
        "severity": severity,
        "rule_text": RULE_TEXT.get(rule, rule),
        "message": message,
        "evidence": {k: v for k, v in evidence.items() if v is not None},
    }


# ---------------------------------------------------------------- helpers
def parse_time(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _aware(stamp, like):
    """Make two datetimes comparable when only one carries a timezone."""
    if stamp is None or like is None:
        return stamp
    if stamp.tzinfo is None and like.tzinfo is not None:
        return stamp.replace(tzinfo=like.tzinfo)
    if stamp.tzinfo is not None and like.tzinfo is None:
        return stamp.replace(tzinfo=None)
    return stamp


def distance_to_drain_m(point, drain):
    """0 inside the geofence, else metres to the centreline."""
    geofence = drain.get("geofence")
    if geofence and geo.point_in_geometry(point, geofence):
        return 0.0

    centreline = drain.get("centreline")
    if centreline:
        return geo.distance_to_line_m(point, centreline)

    # No centreline stored: fall back to the geofence's own vertices.
    if geofence and geofence.get("coordinates"):
        return geo.distance_to_line_m(point, geofence["coordinates"][0])

    return float("inf")


def trace_arrival(points, dumpsite):
    """(entered, arrival time) at the dump site.

    When the truck never gets there, the arrival is its last fix - which is
    what R8 then compares the slip against.
    """
    geofence = (dumpsite or {}).get("geofence")

    if geofence:
        for point in points:
            if geo.point_in_geometry([point["lon"], point["lat"]], geofence):
                return True, parse_time(point.get("t"))

    last = points[-1] if points else None
    return False, parse_time(last.get("t")) if last else None


def max_gap_seconds(points):
    stamps = sorted(filter(None, (parse_time(point.get("t")) for point in points)))
    if len(stamps) < 2:
        return None
    return max(
        (stamps[index + 1] - stamps[index]).total_seconds()
        for index in range(len(stamps) - 1)
    )


def minutes_apart(slip_time_text, arrival):
    """Signed minutes between a slip's 'HH:MM' and a full arrival timestamp.

    The slip only carries a time of day, so the date comes from the arrival;
    the nearest of yesterday / today / tomorrow wins, which keeps a slip
    printed either side of midnight sane.
    """
    if not slip_time_text or arrival is None:
        return None

    try:
        hour, _, minute = str(slip_time_text).partition(":")
        hour, minute = int(hour), int(minute)
    except (TypeError, ValueError):
        return None

    best = None
    for day_shift in (-1, 0, 1):
        candidate = (arrival + datetime.timedelta(days=day_shift)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        delta = (candidate - arrival).total_seconds() / 60
        if best is None or abs(delta) < abs(best):
            best = delta

    return best


# ------------------------------------------------------------ photo rules
def check_photos(drain, photos, duplicates, work_window):
    """R1-R4 over one drain's photos. Returns findings for the whole drain."""
    findings = []

    if not photos:
        findings.append(
            finding("PHOTOS_MISSING", SOFT, "No photo evidence was ingested for this drain.")
        )
        return findings

    window_start, window_end = (parse_time(value) for value in work_window)

    for item in photos:
        key = item.get("s3Key")
        short = key.rsplit("/", 1)[-1] if key else "photo"

        if item.get("status") == "ERROR":
            findings.append(
                finding("EVIDENCE_ERROR", SOFT,
                        f"{short} could not be read during ingestion.",
                        s3Key=key, error=item.get("error"))
            )
            continue

        # ---- R1, photo GPS against the geofence
        if not item.get("hasGps"):
            findings.append(
                finding("R1", SOFT, f"{short} carries no GPS, so its location cannot be checked.",
                        s3Key=key, problems=item.get("problems"))
            )
        else:
            distance = distance_to_drain_m([item["lon"], item["lat"]], drain)
            if distance > config.R1_SOFT_BAND_M:
                findings.append(
                    finding("R1", HARD,
                            f"{short} was taken {distance:.0f} m from the drain, "
                            f"well outside its {config.DRAIN_BUFFER_M:.0f} m geofence.",
                            s3Key=key, distanceM=round(distance, 1))
                )
            elif distance > 0:
                findings.append(
                    finding("R1", SOFT,
                            f"{short} was taken {distance:.0f} m outside the geofence, "
                            f"within the margin of a consumer GPS fix.",
                            s3Key=key, distanceM=round(distance, 1))
                )

        # ---- R2, timestamp against the work window
        taken = parse_time(item.get("timestamp"))
        if taken is None:
            findings.append(
                finding("R2", SOFT, f"{short} has no timestamp, so it cannot be dated.",
                        s3Key=key)
            )
        elif window_start and window_end:
            taken = _aware(taken, window_start)
            if taken < window_start or taken > window_end:
                findings.append(
                    finding("R2", HARD,
                            f"{short} was taken on {taken.date()}, outside the bill's work window.",
                            s3Key=key, timestamp=item.get("timestamp"))
                )

        # ---- R3, reuse
        duplicate_of = duplicates.get(key)
        if duplicate_of:
            findings.append(
                finding("R3", HARD,
                        f"{short} is the same image as {duplicate_of['original'].rsplit('/', 1)[-1]}, "
                        f"already submitted for drain {duplicate_of['drainId']}.",
                        s3Key=key, original=duplicate_of["original"],
                        originalDrainId=duplicate_of["drainId"],
                        hammingDistance=duplicate_of["distance"])
            )

        # ---- R4, what the model saw
        verdict = item.get("bedrock") or {}
        role = item.get("role")
        load_type = verdict.get("load_type")

        if not verdict.get("ok"):
            findings.append(
                finding("R4", SOFT, f"{short} could not be assessed by the vision model.",
                        s3Key=key, notes=verdict.get("notes"))
            )
        elif role == "load" and load_type == "debris":
            findings.append(
                finding("R4", HARD,
                        f"The load in {short} is construction debris, not drain silt.",
                        s3Key=key, confidence=verdict.get("confidence"),
                        notes=verdict.get("notes"))
            )
        elif role == "load" and load_type == "unclear":
            findings.append(
                finding("R4", SOFT, f"The load in {short} could not be identified.",
                        s3Key=key, notes=verdict.get("notes"))
            )
        elif role == "after" and verdict.get("cleared") is False:
            findings.append(
                finding("R4", SOFT,
                        f"{short} is an after-photo, but the channel does not look cleared.",
                        s3Key=key, notes=verdict.get("notes"))
            )

    return findings


def find_duplicates(photos):
    """key -> the earlier photo it copies.

    The earliest appearance of an image is taken as the genuine one and every
    later appearance is a reuse. Without that ordering the rule would have no
    way to say which of two identical photos is the copy.
    """
    usable = [item for item in photos if item.get("pHash")]
    usable.sort(key=lambda item: (item.get("timestamp") or "", item.get("s3Key") or ""))

    duplicates = {}
    for index, item in enumerate(usable):
        for earlier in usable[:index]:
            distance = _hamming(item["pHash"], earlier["pHash"])
            if distance is not None and distance <= PHASH_DUPLICATE_MAX:
                duplicates[item["s3Key"]] = {
                    "original": earlier["s3Key"],
                    "drainId": earlier.get("drainId"),
                    "distance": distance,
                }
                break

    return duplicates


def _hamming(a, b):
    if not a or not b or len(a) != len(b):
        return None
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return None


# ------------------------------------------------------------- trip rules
def check_trip(trip, slip, trace_points, trace_item, dumpsite, vehicles, impossible_pairs):
    """R5-R9 and the GPS-gap flag for one trip."""
    findings = []
    trip_id = trip.get("tripId")

    # ---- the trace
    #
    # No readable trace is never a hard fail. "The truck never reached the
    # dump site" and "we could not read the file" look identical to R5, and
    # only one of them is evidence of anything.
    if not trace_points:
        if trace_item is not None and trace_item.get("status") == "ERROR":
            findings.append(
                finding("EVIDENCE_ERROR", SOFT,
                        "The GPS trace could not be read during ingestion.",
                        traceKey=trip.get("traceKey"), error=trace_item.get("error"))
            )
        elif trace_item is None:
            findings.append(
                finding("TRACE_MISSING", SOFT, "No GPS trace was ingested for this trip.",
                        traceKey=trip.get("traceKey"))
            )
        else:
            findings.append(
                finding("TRACE_MISSING", SOFT,
                        "The GPS trace was ingested but could not be read back, "
                        "so the route cannot be checked.",
                        traceKey=trip.get("traceKey"))
            )
        entered, arrival = False, None
    else:
        entered, arrival = trace_arrival(trace_points, dumpsite)

        # ---- R5
        if not entered:
            findings.append(
                finding("R5", HARD,
                        "The truck's GPS trace never enters the approved dump site.",
                        traceKey=trip.get("traceKey"),
                        lastPoint=([trace_points[-1]["lon"], trace_points[-1]["lat"]]
                                   if trace_points else None))
            )

        # ---- the soft GPS-gap flag
        gap = max_gap_seconds(trace_points)
        if gap is not None and gap > GPS_GAP_SOFT_S:
            findings.append(
                finding("GPS_GAP", SOFT,
                        f"The GPS trace goes dark for {gap / 60:.0f} minutes.",
                        traceKey=trip.get("traceKey"), gapSeconds=round(gap))
            )

    # ---- R6, against the other trips of the same truck
    for other in impossible_pairs.get(trip_id, []):
        overlapping = other["minutes"] <= 0

        if overlapping:
            # The two trips run at the same time, so there is no speed to
            # quote - and no float to round, which infinity does not survive.
            message = (
                f"The same truck is logged on trip {other['otherTripId']} at the "
                f"same time, {other['km']:.1f} km away."
            )
            implied = None
        else:
            message = (
                f"The same truck is logged on trip {other['otherTripId']} "
                f"{other['minutes']:.0f} minutes and {other['km']:.1f} km away, "
                f"which needs {other['impliedSpeedKmh']:.0f} km/h."
            )
            implied = round(other["impliedSpeedKmh"])

        findings.append(
            finding("R6", HARD, message,
                    otherTripId=other["otherTripId"],
                    minutes=round(other["minutes"], 1),
                    km=round(other["km"], 1),
                    overlapping=overlapping or None,
                    impliedSpeedKmh=implied)
        )

    # ---- the slip
    if slip is None:
        findings.append(
            finding("SLIP_MISSING", SOFT, "No weighbridge slip was ingested for this trip.",
                    slipKey=trip.get("slipKey"))
        )
        return findings

    if slip.get("status") == "ERROR":
        findings.append(
            finding("EVIDENCE_ERROR", SOFT, "The weighbridge slip could not be read.",
                    slipKey=trip.get("slipKey"), error=slip.get("error"))
        )
        return findings

    # ---- R7, net weight against the truck
    capacity = vehicles.get(trip.get("vehicleNo"))
    net = slip.get("net")

    if net is None:
        findings.append(
            finding("R7", SOFT, "The slip's net weight could not be read.",
                    slipKey=trip.get("slipKey"),
                    missing=(slip.get("textract") or {}).get("missingFields"))
        )
    elif capacity is not None and net > capacity:
        findings.append(
            finding("R7", HARD,
                    f"The slip claims {net:.2f} t on a truck rated {capacity:.0f} t.",
                    slipKey=trip.get("slipKey"), net=net, capacityTonnes=capacity)
        )

    # ---- R8, slip time against the GPS arrival
    delta = minutes_apart(slip.get("timeIn"), arrival)
    if slip.get("timeIn") is None:
        findings.append(
            finding("R8", SOFT, "The slip's time-in could not be read.",
                    slipKey=trip.get("slipKey"))
        )
    elif delta is None:
        findings.append(
            finding("R8", SOFT, "There is no GPS arrival to compare the slip's time-in against.",
                    slipKey=trip.get("slipKey"))
        )
    elif abs(delta) > SLIP_TOLERANCE_MIN:
        when = "before" if delta < 0 else "after"
        findings.append(
            finding("R8", HARD,
                    f"The slip was printed at {slip['timeIn']}, {abs(delta):.0f} minutes "
                    f"{when} the truck arrived"
                    + ("" if entered else " anywhere") + ".",
                    slipKey=trip.get("slipKey"), timeIn=slip.get("timeIn"),
                    arrival=arrival.isoformat() if arrival else None,
                    minutesEarly=round(-delta, 1))
        )

    # ---- R9, the plate on the slip
    slip_vehicle = slip.get("vehicleNo")
    if slip_vehicle is None:
        findings.append(
            finding("R9", SOFT, "The slip's vehicle number could not be read.",
                    slipKey=trip.get("slipKey"))
        )
    elif trip.get("vehicleNo") and slip_vehicle != trip.get("vehicleNo"):
        findings.append(
            finding("R9", HARD,
                    f"The slip is for {slip_vehicle}, but the trip is logged to "
                    f"{trip['vehicleNo']}.",
                    slipKey=trip.get("slipKey"), slipVehicleNo=slip_vehicle,
                    tripVehicleNo=trip.get("vehicleNo"))
        )

    return findings


def find_impossible_pairs(trips, traces):
    """tripId -> the other trips of its truck it cannot physically follow (R6).

    Both trips in a pair are flagged: the records contradict each other and
    nothing in the data says which one is the lie.
    """
    by_vehicle = {}
    for trip in trips:
        points = traces.get(trip.get("traceKey")) or []
        if not points:
            continue

        start = parse_time(points[0].get("t"))
        end = parse_time(points[-1].get("t"))
        if start is None or end is None:
            continue

        by_vehicle.setdefault(trip.get("vehicleNo"), []).append(
            {
                "tripId": trip.get("tripId"),
                "start": start,
                "end": end,
                "from": [points[0]["lon"], points[0]["lat"]],
                "to": [points[-1]["lon"], points[-1]["lat"]],
            }
        )

    pairs = {}
    for legs in by_vehicle.values():
        legs.sort(key=lambda leg: leg["start"])

        for first, second in zip(legs, legs[1:]):
            minutes = (second["start"] - first["end"]).total_seconds() / 60
            km = geo.haversine_m(first["to"], second["from"]) / 1000

            if minutes <= 0:
                speed = float("inf")
            else:
                speed = km / (minutes / 60)

            if speed <= SPEED_LIMIT_KMH:
                continue

            pairs.setdefault(first["tripId"], []).append(
                {"otherTripId": second["tripId"], "minutes": minutes, "km": km,
                 "impliedSpeedKmh": speed}
            )
            pairs.setdefault(second["tripId"], []).append(
                {"otherTripId": first["tripId"], "minutes": minutes, "km": km,
                 "impliedSpeedKmh": speed}
            )

    return pairs


# ------------------------------------------------------------ drain rules
def check_drain_volume(drain, claimed_tonnes):
    """R10: does the claimed tonnage fit the hole it came out of?"""
    ceiling = drain.get("plausibleMaxTonnes")

    if ceiling is None:
        length = drain.get("lengthM")
        width = drain.get("widthM")
        depth = drain.get("depthM")
        if None in (length, width, depth):
            return []
        ceiling = length * width * depth * config.SILT_DENSITY * R10_ALLOWANCE

    if claimed_tonnes > ceiling:
        return [
            finding("R10", SOFT,
                    f"The drain claims {claimed_tonnes:.0f} t, more than the "
                    f"{ceiling:.0f} t its length, width and depth allow.",
                    claimedTonnes=claimed_tonnes, plausibleMaxTonnes=round(ceiling, 1))
        ]

    return []


# ---------------------------------------------------------------- rollups
def verdict_for(findings):
    if any(item["severity"] == HARD for item in findings):
        return HOLD
    if findings:
        return REVIEW
    return VERIFIED


def rule_ids(findings, severity):
    return sorted({item["rule"] for item in findings if item["severity"] == severity})


def colour_for(trip_verdicts):
    if HOLD in trip_verdicts:
        return RED
    if REVIEW in trip_verdicts:
        return AMBER
    return GREEN


def apply_decision(decision, verified, review, held):
    """How an engineer's call moves money (see DECISIONS.md).

    APPROVE releases everything the rules withheld on that drain; HOLD pushes
    the pending review tonnage across to held. Without a decision the
    evidence stands on its own.
    """
    if decision == "APPROVE":
        return round(verified + review + held, 3), 0.0, 0.0
    if decision == "HOLD":
        return verified, 0.0, round(review + held, 3)
    return verified, review, held


# ------------------------------------------------------------------ entry
def evaluate(context):
    """Run every rule over one bill.

    context keys:
        bill       the BILL#.../META item
        drains     list of drain items
        trips      list of trip items
        photos     list of PHOTO evidence items
        slips      {s3 key: SLIP evidence item}
        traceItems {s3 key: TRACE evidence item}
        traces     {s3 key: [points]}
        dumpsite   the DUMPSITE#.../META item
        vehicles   {vehicleNo: capacityTonnes}
    """
    bill = context.get("bill") or {}
    work_window = (bill.get("workWindowStart"), bill.get("workWindowEnd"))
    rate = bill.get("ratePerTonne", config.RATE_PER_TONNE)

    photos = context.get("photos") or []
    duplicates = find_duplicates(photos)
    photos_by_drain = {}
    for item in photos:
        photos_by_drain.setdefault(item.get("drainId"), []).append(item)

    trips = context.get("trips") or []
    impossible = find_impossible_pairs(trips, context.get("traces") or {})
    trips_by_drain = {}
    for trip in trips:
        trips_by_drain.setdefault(trip.get("drainId"), []).append(trip)

    drain_results = {}
    trip_results = {}

    for drain in context.get("drains") or []:
        drain_id = drain.get("drainId")
        drain_trips = sorted(
            trips_by_drain.get(drain_id, []), key=lambda trip: trip.get("tripNo") or ""
        )
        claimed = sum(trip.get("claimedTonnes") or 0 for trip in drain_trips)

        drain_findings = check_photos(
            drain, photos_by_drain.get(drain_id, []), duplicates, work_window
        )
        drain_findings += check_drain_volume(drain, claimed)

        verified = review = held = 0.0
        verdicts = []

        for trip in drain_trips:
            own = check_trip(
                trip,
                (context.get("slips") or {}).get(trip.get("slipKey")),
                (context.get("traces") or {}).get(trip.get("traceKey")) or [],
                (context.get("traceItems") or {}).get(trip.get("traceKey")),
                context.get("dumpsite"),
                context.get("vehicles") or {},
                impossible,
            )
            combined = drain_findings + own
            verdict = verdict_for(combined)
            tonnes = trip.get("claimedTonnes") or 0

            if verdict == HOLD:
                held += tonnes
            elif verdict == REVIEW:
                review += tonnes
            else:
                verified += tonnes

            verdicts.append(verdict)
            trip_results[trip.get("tripId")] = {
                "tripId": trip.get("tripId"),
                "drainId": drain_id,
                "tripNo": trip.get("tripNo"),
                "vehicleNo": trip.get("vehicleNo"),
                "claimedTonnes": tonnes,
                "verdict": verdict,
                "hardFails": rule_ids(combined, HARD),
                "softFails": rule_ids(combined, SOFT),
                "findings": combined,
                "fromDrainEvidence": rule_ids(drain_findings, HARD)
                + rule_ids(drain_findings, SOFT),
            }

        decision = drain.get("decision")
        decided = apply_decision(decision, round(verified, 3), round(review, 3), round(held, 3))

        drain_results[drain_id] = {
            "drainId": drain_id,
            "name": drain.get("name"),
            "claimedTonnes": round(claimed, 3),
            "verdict": colour_for(verdicts),
            "decision": decision,
            "note": drain.get("note"),
            "tripCount": len(drain_trips),
            "evidenceVerified": round(verified, 3),
            "evidenceReview": round(review, 3),
            "evidenceHeld": round(held, 3),
            "verifiedTonnes": decided[0],
            "reviewTonnes": decided[1],
            "heldTonnes": decided[2],
            "findings": drain_findings,
            "failedRules": sorted({item["rule"] for item in drain_findings}),
        }

    summary = summarise(drain_results.values(), rate)

    return {"drains": drain_results, "trips": trip_results, "summary": summary}


def summarise(drains, rate=config.RATE_PER_TONNE):
    drains = list(drains)

    def total(name):
        return round(sum(drain.get(name) or 0 for drain in drains), 3)

    claimed = total("claimedTonnes")
    verified = total("verifiedTonnes")
    review = total("reviewTonnes")
    held = total("heldTonnes")

    return {
        "ratePerTonne": rate,
        "claimedTonnes": claimed,
        "verifiedTonnes": verified,
        "reviewTonnes": review,
        "heldTonnes": held,
        "claimedRupees": int(round(claimed * rate)),
        "verifiedRupees": int(round(verified * rate)),
        "reviewRupees": int(round(review * rate)),
        "heldRupees": int(round(held * rate)),
        "drainCount": len(drains),
        "red": sum(1 for drain in drains if drain.get("verdict") == RED),
        "amber": sum(1 for drain in drains if drain.get("verdict") == AMBER),
        "green": sum(1 for drain in drains if drain.get("verdict") == GREEN),
        "decided": sum(1 for drain in drains if drain.get("decision")),
    }
