"""Checks over one trial's evidence (docs/JUDGE-TRIAL-API.md section 11).

The B1 rules in ``common.rules`` only ever *report failures*: absence of a
finding means "passed", and missing geometry reads as "infinitely far away".
That is right for a seeded bill where every drain has a surveyed geofence,
and wrong for a judge who uploaded one photo. So this module checks each
rule's prerequisites first, and only then calls the B1 helpers:

* prerequisite absent           -> NOT_EVALUATED, with what is missing
* evidence present, undecidable -> INCONCLUSIVE
* rule ran on an approximate reference with no contradiction -> CONSISTENT
* otherwise PASS / REVIEW (soft) / FAIL (hard), on the B1 thresholds

No check here calls an AWS service, and none edits ``common.rules``.
"""

import math

from common import config, geo, rules

from . import validate

PASS = "PASS"
CONSISTENT = "CONSISTENT"
REVIEW = "REVIEW"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"
NOT_EVALUATED = "NOT_EVALUATED"
STATUSES = (PASS, CONSISTENT, REVIEW, FAIL, INCONCLUSIVE, NOT_EVALUATED)

RULES_VERSION = "trial-1"

TITLES = {
    "R1": "Photo GPS against the drain location",
    "R2": "Photo taken inside the work window",
    "R3": "Photo not reused within this trial",
    "R4": "After-photo shows a cleared channel",
    "R5": "GPS trace enters the designated disposal site",
    "R6": "Consecutive trips of one truck are physically possible",
    "R7": "Slip net weight within the truck's capacity",
    "R8": "Slip time-in matches the GPS arrival",
    "R9": "Slip vehicle matches the trip's truck",
    "R10": "Claimed quantity fits the drain's volume",
    "GPS_GAP": "GPS trace does not go dark for more than three minutes",
    "T1": "Claim arithmetic: quantity × rate = amount",
    "T2": "Slip arithmetic: gross − tare = net",
    "T3": "Slips account for the claimed quantity",
}

# The B1 margin beyond the geofence that is a soft, not hard, R1 failure.
R1_MARGIN_M = config.R1_SOFT_BAND_M - config.DRAIN_BUFFER_M

T1_TOLERANCE_RUPEES = 1.0
T1_TOLERANCE_FRACTION = 0.005
T2_TOLERANCE_TONNES = 0.05
T3_COVERED_FRACTION = 0.98


def make_check(rule, status, message, *, subject=None, severity=None, missing=(),
               basis=None, evidence=None):
    assert status in STATUSES
    return {
        "id": rule,
        "title": TITLES[rule],
        "subject": subject,
        "status": status,
        "severity": severity if status in (FAIL, REVIEW) else None,
        "message": message,
        "missing": list(missing),
        "basis": basis,
        "evidence": {k: v for k, v in (evidence or {}).items() if v is not None},
    }


def _subject(item):
    return {"evidenceId": item["evidenceId"], "filename": item.get("filename"),
            "group": item.get("group"), "role": item.get("role")}


def _name(item):
    return item.get("filename") or item["evidenceId"]


def _num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


# ------------------------------------------------------------- geometry
def circle(point, radius_m, segments=48):
    ring = [
        geo.offset_m(point, radius_m * math.cos(2 * math.pi * index / segments),
                     radius_m * math.sin(2 * math.pi * index / segments))
        for index in range(segments)
    ]
    ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def disposal_geofence(site):
    if not site:
        return None
    if site.get("polygon"):
        return {"type": "Polygon", "coordinates": [site["polygon"]]}
    return circle(site["point"], site.get("radiusM", 150))


def drain_distance_m(point, drain):
    if drain.get("line"):
        return geo.distance_to_line_m(point, drain["line"])
    return geo.haversine_m(point, drain["point"])


# ---------------------------------------------------------------- photos
def check_r1(photo_item, drain):
    exif = (photo_item.get("result") or {}).get("exif") or {}
    missing = []
    if not exif.get("hasGps"):
        missing.append("GPS coordinates in the photo's EXIF")
    if not drain:
        missing.append("a drain location")
    if missing:
        return make_check("R1", NOT_EVALUATED,
                          "Not evaluated: insufficient evidence.",
                          subject=_subject(photo_item), missing=missing)

    point = [exif["lon"], exif["lat"]]
    distance = drain_distance_m(point, drain)
    tolerance = float(drain.get("toleranceM", config.DRAIN_BUFFER_M))
    accuracy = exif.get("gpsAccuracyM")
    reference = "line" if drain.get("line") else "point"
    evidence = {"distanceM": round(distance, 1), "toleranceM": tolerance,
                "marginM": R1_MARGIN_M, "gpsAccuracyM": accuracy,
                "reference": reference, "source": drain.get("source")}
    name = _name(photo_item)

    if distance > tolerance + R1_MARGIN_M:
        return make_check(
            "R1", FAIL,
            f"{name} was taken {distance:,.0f} m from the supplied drain {reference}, "
            f"beyond its {tolerance:.0f} m tolerance and the {R1_MARGIN_M:.0f} m margin.",
            subject=_subject(photo_item), severity=rules.HARD, basis="approximate",
            evidence=evidence)
    if distance > tolerance:
        return make_check(
            "R1", REVIEW,
            f"{name} was taken {distance:.0f} m from the supplied drain {reference}, "
            f"{distance - tolerance:.0f} m outside its {tolerance:.0f} m tolerance: within "
            "the margin of a consumer GPS fix, so a person should look.",
            subject=_subject(photo_item), severity=rules.SOFT, basis="approximate",
            evidence=evidence)
    if drain.get("derivedFromPhotos"):
        return make_check(
            "R1", INCONCLUSIVE,
            f"{name} is {distance:.0f} m from the drain {reference}, but that {reference} was "
            "drawn from these same photographs, so the comparison is circular and proves nothing.",
            subject=_subject(photo_item), basis="approximate", evidence=evidence)
    if _num(accuracy) and accuracy > tolerance:
        return make_check(
            "R1", INCONCLUSIVE,
            f"{name} is {distance:.0f} m from the drain {reference}, but the phone reports a "
            f"GPS accuracy of {accuracy:.0f} m, wider than the {tolerance:.0f} m tolerance.",
            subject=_subject(photo_item), basis="approximate", evidence=evidence)

    accuracy_note = (f" The phone reports {accuracy:.0f} m accuracy." if _num(accuracy)
                     else " The photo does not record its GPS accuracy.")
    return make_check(
        "R1", CONSISTENT,
        f"{name} is {distance:.0f} m from the supplied drain {reference}, inside its "
        f"{tolerance:.0f} m tolerance. The location was supplied for this trial and is not an "
        f"official drain map, so this is consistency, not proof.{accuracy_note}",
        subject=_subject(photo_item), basis="approximate", evidence=evidence)


def check_r2(photo_item, window):
    exif = (photo_item.get("result") or {}).get("exif") or {}
    missing = []
    if not exif.get("timestamp"):
        missing.append("a capture time in the photo's EXIF")
    if not window:
        missing.append("a work window")
    if missing:
        return make_check("R2", NOT_EVALUATED, "Not evaluated: insufficient evidence.",
                          subject=_subject(photo_item), missing=missing)

    start = rules.parse_time(window["start"])
    end = rules.parse_time(window["end"])
    taken = rules._aware(rules.parse_time(exif["timestamp"]), start)
    if taken is None or start is None or end is None:
        return make_check("R2", INCONCLUSIVE, "The capture time could not be read as a date.",
                          subject=_subject(photo_item), basis="extracted")

    assumed = not exif.get("timestampHasOffset") and start.tzinfo is not None
    note = (" The photo's time carries no timezone, so it was read in the work window's "
            "timezone." if assumed else "")
    evidence = {"timestamp": exif["timestamp"], "windowStart": window["start"],
                "windowEnd": window["end"], "timezoneAssumed": assumed or None}
    name = _name(photo_item)
    if taken < start or taken > end:
        return make_check(
            "R2", FAIL,
            f"{name} was taken at {taken.strftime('%Y-%m-%d %H:%M')}, outside the work window.{note}",
            subject=_subject(photo_item), severity=rules.HARD, basis="extracted",
            evidence=evidence)
    return make_check(
        "R2", PASS,
        f"{name} was taken at {taken.strftime('%Y-%m-%d %H:%M')}, inside the work window.{note}",
        subject=_subject(photo_item), basis="extracted", evidence=evidence)


def check_r3(photos):
    hashed = [item for item in photos if ((item.get("result") or {}).get("pHash"))]
    if len(hashed) < 2:
        return [make_check(
            "R3", NOT_EVALUATED, "Not evaluated: insufficient evidence.",
            missing=["at least two photographs with a readable image hash"])]

    usable = [
        {"s3Key": item["evidenceId"], "pHash": item["result"]["pHash"],
         "timestamp": (item["result"].get("exif") or {}).get("timestamp"), "drainId": None}
        for item in hashed
    ]
    duplicates = rules.find_duplicates(usable)
    by_id = {item["evidenceId"]: item for item in hashed}
    checks = []
    for item in hashed:
        copy = duplicates.get(item["evidenceId"])
        if copy:
            original = by_id[copy["original"]]
            checks.append(make_check(
                "R3", FAIL,
                f"{_name(item)} is the same image as {_name(original)} "
                f"({copy['distance']} of 64 hash bits differ; the threshold is "
                f"{rules.PHASH_DUPLICATE_MAX}).",
                subject=_subject(item), severity=rules.HARD, basis="extracted",
                evidence={"originalEvidenceId": original["evidenceId"],
                          "hammingDistance": copy["distance"]}))
        else:
            checks.append(make_check(
                "R3", PASS,
                f"{_name(item)} does not match any other photo in this trial. "
                "Trials are not compared with each other or with Bill B1.",
                subject=_subject(item), basis="extracted"))
    return checks


def check_r4(photos):
    after = [item for item in photos if item.get("role") == "after"]
    if not after:
        return [make_check(
            "R4", NOT_EVALUATED, "Not evaluated: insufficient evidence.",
            missing=["a photograph labelled 'after'"])]

    checks = []
    for item in after:
        vision = (item.get("result") or {}).get("vision") or {}
        basis = "mocked" if vision.get("mocked") else "model"
        if not vision.get("ran"):
            checks.append(make_check(
                "R4", NOT_EVALUATED, "Not evaluated: the model did not read this photo.",
                subject=_subject(item),
                missing=[f"a model result ({vision.get('skippedReason') or 'not run'})"]))
        elif not vision.get("ok") or vision.get("cleared") is None:
            checks.append(make_check(
                "R4", INCONCLUSIVE,
                f"The model's answer for {_name(item)} did not fit the expected form, so it "
                "cannot decide whether the channel is cleared.",
                subject=_subject(item), basis=basis))
        elif vision.get("cleared") is False:
            checks.append(make_check(
                "R4", REVIEW,
                f"{_name(item)} is labelled 'after', but the model does not see a cleared "
                f"channel (confidence {vision.get('confidence')}).",
                subject=_subject(item), severity=rules.SOFT, basis=basis,
                evidence={"confidence": vision.get("confidence")}))
        else:
            checks.append(make_check(
                "R4", PASS,
                f"The model sees a cleared channel in {_name(item)} "
                f"(confidence {vision.get('confidence')}). A model reading is an observation, "
                "not proof that desilting happened.",
                subject=_subject(item), basis=basis,
                evidence={"confidence": vision.get("confidence")}))
    return checks


# ------------------------------------------------------------ slips/traces
def slip_fields(item):
    return ((item.get("result") or {}).get("fields")) or {}


def slip_value(item, name):
    return (slip_fields(item).get(name) or {}).get("value")


def slip_basis(item):
    return "mocked" if (item.get("result") or {}).get("mocked") else "extracted"


def effective_vehicle(trace_item, truck):
    vehicle = ((trace_item.get("result") or {}).get("vehicleNo"))
    return validate.normalise_vehicle(vehicle or (truck or {}).get("vehicleNo"))


def pair_slips(slips, traces, truck):
    """slip evidenceId -> trace evidenceId. One-and-one pair directly; otherwise
    a slip pairs with the unused trace whose vehicle number matches."""
    if len(slips) == 1 and len(traces) == 1:
        return {slips[0]["evidenceId"]: traces[0]["evidenceId"]}
    pairs, used = {}, set()
    for slip in slips:
        vehicle = validate.normalise_vehicle(slip_value(slip, "vehicleNo"))
        if not vehicle:
            continue
        for trace in traces:
            if trace["evidenceId"] in used:
                continue
            if effective_vehicle(trace, truck) == vehicle:
                pairs[slip["evidenceId"]] = trace["evidenceId"]
                used.add(trace["evidenceId"])
                break
    return pairs


def check_trace(trace_item, points, site):
    checks = []
    geofence = disposal_geofence(site)
    if not geofence:
        checks.append(make_check(
            "R5", NOT_EVALUATED, "Not evaluated: insufficient evidence.",
            subject=_subject(trace_item), missing=["a designated disposal site"]))
    else:
        entered, arrival = rules.trace_arrival(points, {"geofence": geofence})
        if entered:
            checks.append(make_check(
                "R5", PASS,
                f"The trace enters the designated disposal site at "
                f"{arrival.strftime('%H:%M') if arrival else 'an untimed fix'}. The site was "
                "designated for this trial; that does not make it an authorised landfill.",
                subject=_subject(trace_item), basis="supplied",
                evidence={"arrival": arrival.isoformat() if arrival else None}))
        else:
            last = points[-1]
            checks.append(make_check(
                "R5", FAIL,
                "The trace never enters the designated disposal site. Its last fix is "
                f"{geo.haversine_m([last['lon'], last['lat']], site['point']) / 1000:.1f} km "
                "from the site's centre.",
                subject=_subject(trace_item), severity=rules.HARD, basis="supplied",
                evidence={"lastFix": [last["lon"], last["lat"]], "lastFixTime": last.get("t")}))

    gap = rules.max_gap_seconds(points)
    if gap is None:
        checks.append(make_check("GPS_GAP", INCONCLUSIVE, "The trace has too few timed points.",
                                 subject=_subject(trace_item), basis="supplied"))
    elif gap > rules.GPS_GAP_SOFT_S:
        checks.append(make_check(
            "GPS_GAP", REVIEW, f"The trace goes dark for {gap / 60:.0f} minutes.",
            subject=_subject(trace_item), severity=rules.SOFT, basis="supplied",
            evidence={"gapSeconds": round(gap)}))
    else:
        checks.append(make_check(
            "GPS_GAP", PASS, f"The longest gap between fixes is {gap:.0f} s.",
            subject=_subject(trace_item), basis="supplied", evidence={"gapSeconds": round(gap)}))
    return checks


def check_r6(traces, points_by_id, truck):
    trips = []
    by_vehicle = {}
    for item in traces:
        vehicle = effective_vehicle(item, truck)
        if not vehicle:
            continue
        by_vehicle.setdefault(vehicle, []).append(item)
        trips.append({"tripId": item["evidenceId"], "vehicleNo": vehicle,
                      "traceKey": item["evidenceId"]})
    eligible = [items for items in by_vehicle.values() if len(items) >= 2]
    if not eligible:
        return [make_check(
            "R6", NOT_EVALUATED, "Not evaluated: insufficient evidence.",
            missing=["two or more traces for the same truck"])]

    pairs = rules.find_impossible_pairs(trips, points_by_id)
    names = {item["evidenceId"]: _name(item) for item in traces}
    checks = []
    for items in eligible:
        for item in items:
            clashes = pairs.get(item["evidenceId"]) or []
            if clashes:
                other = clashes[0]
                checks.append(make_check(
                    "R6", FAIL,
                    f"{_name(item)} and {names.get(other['otherTripId'])} put the same truck "
                    f"{other['km']:.1f} km apart with only {max(other['minutes'], 0):.0f} "
                    f"minutes between them (limit {rules.SPEED_LIMIT_KMH:.0f} km/h).",
                    subject=_subject(item), severity=rules.HARD, basis="supplied",
                    evidence={"otherEvidenceId": other["otherTripId"],
                              "km": round(other["km"], 2),
                              "minutes": round(other["minutes"], 1)}))
            else:
                checks.append(make_check(
                    "R6", PASS, f"{_name(item)} does not clash with the truck's other traces.",
                    subject=_subject(item), basis="supplied"))
    return checks


def check_slip(slip, trace_item, points, site, truck):
    checks = []
    basis = slip_basis(slip)
    net = slip_value(slip, "net")
    capacity = (truck or {}).get("capacityTonnes")

    # ---- R7
    if net is None:
        checks.append(make_check("R7", INCONCLUSIVE, "The slip's net weight could not be read.",
                                 subject=_subject(slip), basis=basis))
    elif capacity is None:
        checks.append(make_check("R7", NOT_EVALUATED, "Not evaluated: insufficient evidence.",
                                 subject=_subject(slip), missing=["the truck's rated capacity"]))
    elif net > capacity:
        checks.append(make_check(
            "R7", FAIL, f"The slip claims {net:.2f} t on a truck rated {capacity:g} t.",
            subject=_subject(slip), severity=rules.HARD, basis=basis,
            evidence={"net": net, "capacityTonnes": capacity}))
    else:
        checks.append(make_check(
            "R7", PASS, f"Net {net:.2f} t is within the truck's rated {capacity:g} t.",
            subject=_subject(slip), basis=basis, evidence={"net": net, "capacityTonnes": capacity}))

    # ---- R8
    time_in = slip_value(slip, "timeIn")
    missing = []
    if trace_item is None:
        missing.append("a GPS trace paired with this slip (same vehicle number)")
    if not site:
        missing.append("a designated disposal site")
    if time_in is None:
        checks.append(make_check("R8", INCONCLUSIVE, "The slip's time-in could not be read.",
                                 subject=_subject(slip), basis=basis))
    elif missing:
        checks.append(make_check("R8", NOT_EVALUATED, "Not evaluated: insufficient evidence.",
                                 subject=_subject(slip), missing=missing))
    else:
        entered, arrival = rules.trace_arrival(points, {"geofence": disposal_geofence(site)})
        delta = rules.minutes_apart(time_in, arrival)
        if delta is None:
            checks.append(make_check("R8", INCONCLUSIVE,
                                     "There is no timed GPS fix to compare the slip against.",
                                     subject=_subject(slip), basis=basis))
        elif abs(delta) > rules.SLIP_TOLERANCE_MIN:
            departure = rules.parse_time(points[0].get("t"))
            checks.append(make_check(
                "R8", FAIL, rules.r8_message(time_in, delta, arrival, entered, departure),
                subject=_subject(slip), severity=rules.HARD, basis=basis,
                evidence={"timeIn": time_in, "arrival": arrival.isoformat(),
                          "arrivalKind": "disposalSite" if entered else "lastFix",
                          "minutes": round(delta, 1), "traceEvidenceId": trace_item["evidenceId"]}))
        else:
            checks.append(make_check(
                "R8", PASS,
                f"The slip's time-in {time_in} is within {rules.SLIP_TOLERANCE_MIN:.0f} minutes "
                f"of the GPS {'arrival at the site' if entered else 'last fix'} at "
                f"{arrival.strftime('%H:%M')}.",
                subject=_subject(slip), basis=basis,
                evidence={"minutes": round(delta, 1), "arrivalKind":
                          "disposalSite" if entered else "lastFix"}))

    # ---- R9
    slip_vehicle = validate.normalise_vehicle(slip_value(slip, "vehicleNo"))
    trip_vehicle = effective_vehicle(trace_item, truck) if trace_item else \
        validate.normalise_vehicle((truck or {}).get("vehicleNo"))
    if slip_vehicle is None:
        checks.append(make_check("R9", INCONCLUSIVE, "The slip's vehicle number could not be read.",
                                 subject=_subject(slip), basis=basis))
    elif trip_vehicle is None:
        checks.append(make_check("R9", NOT_EVALUATED, "Not evaluated: insufficient evidence.",
                                 subject=_subject(slip),
                                 missing=["a vehicle number from a trace or the truck details"]))
    elif slip_vehicle != trip_vehicle:
        checks.append(make_check(
            "R9", FAIL, f"The slip is for {slip_vehicle}, but the trip's truck is {trip_vehicle}.",
            subject=_subject(slip), severity=rules.HARD, basis=basis,
            evidence={"slipVehicleNo": slip_vehicle, "tripVehicleNo": trip_vehicle}))
    else:
        checks.append(make_check("R9", PASS, f"The slip and the trip both name {slip_vehicle}.",
                                 subject=_subject(slip), basis=basis))

    # ---- T2
    gross, tare = slip_value(slip, "gross"), slip_value(slip, "tare")
    derived = (slip_fields(slip).get("net") or {}).get("derived")
    if None in (gross, tare, net):
        checks.append(make_check(
            "T2", INCONCLUSIVE,
            "Gross, tare and net could not all be read, so the slip's arithmetic cannot be checked.",
            subject=_subject(slip), basis=basis))
    elif derived:
        checks.append(make_check(
            "T2", INCONCLUSIVE,
            "The net weight was not read from the slip; it was computed as gross − tare, so "
            "checking it against gross − tare would be circular.",
            subject=_subject(slip), basis=basis))
    elif abs(gross - tare - net) > T2_TOLERANCE_TONNES:
        checks.append(make_check(
            "T2", FAIL,
            f"Gross {gross:.2f} t − tare {tare:.2f} t = {gross - tare:.2f} t, but the slip "
            f"prints net {net:.2f} t.",
            subject=_subject(slip), severity=rules.HARD, basis=basis,
            evidence={"gross": gross, "tare": tare, "net": net}))
    else:
        checks.append(make_check(
            "T2", PASS, f"Gross {gross:.2f} t − tare {tare:.2f} t matches net {net:.2f} t.",
            subject=_subject(slip), basis=basis))
    return checks


# ------------------------------------------------------------------ claim
def check_claim(claim, drain, slips):
    checks = []
    claim = claim or {}
    quantity = claim.get("quantityTonnes")
    rate = claim.get("ratePerTonne")
    amount = claim.get("amountRupees")

    # ---- T1
    present = {name: value for name, value in
               (("quantity", quantity), ("rate", rate), ("amount", amount)) if value is not None}
    if len(present) < 3:
        checks.append(make_check(
            "T1", NOT_EVALUATED, "Not evaluated: insufficient evidence.",
            missing=[f"the claimed {name}" for name in ("quantity", "rate", "amount")
                     if name not in present]))
    else:
        expected = quantity * rate
        tolerance = max(T1_TOLERANCE_RUPEES, T1_TOLERANCE_FRACTION * expected)
        if abs(expected - amount) > tolerance:
            checks.append(make_check(
                "T1", FAIL,
                f"{quantity:g} t × ₹{rate:,.2f}/t = ₹{expected:,.2f}, but the claim is "
                f"₹{amount:,.2f} (a difference of ₹{amount - expected:,.2f}).",
                severity=rules.HARD, basis="supplied",
                evidence={"expectedRupees": round(expected, 2), "claimedRupees": amount}))
        else:
            checks.append(make_check(
                "T1", PASS,
                f"{quantity:g} t × ₹{rate:,.2f}/t = ₹{expected:,.2f}, matching the claim.",
                basis="supplied", evidence={"expectedRupees": round(expected, 2)}))

    # ---- R10
    dims = [(drain or {}).get(name) for name in ("lengthM", "widthM", "depthM")]
    missing = []
    if quantity is None:
        missing.append("the claimed quantity")
    if not drain or None in dims:
        missing.append("the drain's length, width and depth")
    if missing:
        checks.append(make_check("R10", NOT_EVALUATED, "Not evaluated: insufficient evidence.",
                                 missing=missing))
    else:
        found = rules.check_drain_volume(
            {"lengthM": dims[0], "widthM": dims[1], "depthM": dims[2]}, quantity)
        ceiling = dims[0] * dims[1] * dims[2] * config.SILT_DENSITY * rules.R10_ALLOWANCE
        if found:
            checks.append(make_check("R10", REVIEW, found[0]["message"], severity=rules.SOFT,
                                     basis="supplied",
                                     evidence={"plausibleMaxTonnes": round(ceiling, 1)}))
        else:
            checks.append(make_check(
                "R10", PASS,
                f"{quantity:g} t is within the {ceiling:,.0f} t that the stated dimensions allow "
                f"({config.SILT_DENSITY} t/m³, ×{rules.R10_ALLOWANCE} headroom).",
                basis="supplied", evidence={"plausibleMaxTonnes": round(ceiling, 1)}))

    # ---- T3
    nets = [slip_value(slip, "net") for slip in slips]
    read = [net for net in nets if net is not None]
    if quantity is None or not slips:
        checks.append(make_check(
            "T3", NOT_EVALUATED, "Not evaluated: insufficient evidence.",
            missing=([] if quantity is not None else ["the claimed quantity"])
            + ([] if slips else ["a weighbridge slip"])))
    elif not read:
        checks.append(make_check("T3", INCONCLUSIVE, "No slip's net weight could be read.",
                                 basis="extracted"))
    else:
        total = round(sum(read), 3)
        unread = len(nets) - len(read)
        note = f" {unread} slip(s) had no readable net weight." if unread else ""
        basis = "mocked" if any(slip_basis(slip) == "mocked" for slip in slips) else "extracted"
        if total >= quantity * T3_COVERED_FRACTION:
            checks.append(make_check(
                "T3", PASS, f"The slips' net weights total {total:g} t against {quantity:g} t "
                f"claimed.{note}", basis=basis,
                evidence={"slipTotalTonnes": total, "claimedTonnes": quantity}))
        else:
            checks.append(make_check(
                "T3", REVIEW,
                f"The slips account for {total:g} t of the {quantity:g} t claimed "
                f"({total / quantity:.0%}).{note}",
                severity=rules.SOFT, basis=basis,
                evidence={"slipTotalTonnes": total, "claimedTonnes": quantity}))
    return checks


# ------------------------------------------------------------------ entry
def demote_mocked(check):
    """A rule outcome computed from an offline fixture is not an outcome.

    The rule logic still runs, so an offline demo shows what it would say,
    but the status is NOT_EVALUATED and the would-be result is kept apart.
    """
    if check["basis"] != "mocked" or check["status"] in (NOT_EVALUATED, INCONCLUSIVE):
        return check
    return {
        **check,
        "status": NOT_EVALUATED,
        "severity": None,
        "message": "OFFLINE MOCK, no real reading: the rule ran on test-fixture values and "
                   f"would say: {check['message']}",
        "missing": ["a real Amazon Textract or Bedrock reading (this run used offline mocks)"],
        "mockOutcome": check["status"],
    }


def evaluate(trial, evidence, trace_points):
    """All checks for one trial.

    trial         the META item (its ``details``)
    evidence      READY evidence items
    trace_points  {evidenceId: [points]} for READY traces
    """
    details = trial.get("details") or {}
    drain = details.get("drainLocation")
    site = details.get("disposalSite")
    window = details.get("workWindow")
    truck = details.get("truck")
    claim = details.get("claim")

    photos = [item for item in evidence if item["group"] == "photo"]
    slips = [item for item in evidence if item["group"] == "slip"]
    traces = [item for item in evidence if item["group"] == "trace"
              and item["evidenceId"] in trace_points]

    checks = []
    if photos:
        for item in photos:
            checks.append(check_r1(item, drain))
            checks.append(check_r2(item, window))
    else:
        checks.append(make_check("R1", NOT_EVALUATED, "Not evaluated: insufficient evidence.",
                                 missing=["a drain photograph"]
                                 + ([] if drain else ["a drain location"])))
        checks.append(make_check("R2", NOT_EVALUATED, "Not evaluated: insufficient evidence.",
                                 missing=["a drain photograph"]
                                 + ([] if window else ["a work window"])))
    checks += check_r3(photos)
    checks += check_r4(photos)

    if traces:
        for item in traces:
            checks += check_trace(item, trace_points[item["evidenceId"]], site)
    else:
        checks.append(make_check("R5", NOT_EVALUATED, "Not evaluated: insufficient evidence.",
                                 missing=["a truck GPS trace"]
                                 + ([] if site else ["a designated disposal site"])))
        checks.append(make_check("GPS_GAP", NOT_EVALUATED,
                                 "Not evaluated: insufficient evidence.",
                                 missing=["a truck GPS trace"]))
    checks += check_r6(traces, trace_points, truck)

    if slips:
        pairs = pair_slips(slips, traces, truck)
        by_id = {item["evidenceId"]: item for item in traces}
        for slip in slips:
            trace = by_id.get(pairs.get(slip["evidenceId"]))
            points = trace_points.get(trace["evidenceId"]) if trace else None
            checks += check_slip(slip, trace, points, site, truck)
    else:
        for rule in ("R7", "R8", "R9", "T2"):
            checks.append(make_check(rule, NOT_EVALUATED, "Not evaluated: insufficient evidence.",
                                     missing=["a weighbridge slip"]))

    checks += check_claim(claim, drain, slips)
    checks = [demote_mocked(check) for check in checks]

    order = list(TITLES)
    checks.sort(key=lambda check: order.index(check["id"]))
    counts = {status: sum(1 for check in checks if check["status"] == status)
              for status in STATUSES}
    return checks, counts


def observations(evidence):
    """What the services returned, kept apart from the rule checks."""
    out = []
    for item in evidence:
        result = item.get("result") or {}
        if item["group"] == "photo":
            out.append({"evidenceId": item["evidenceId"], "filename": item.get("filename"),
                        "kind": "vision", "role": item.get("role"),
                        "vision": result.get("vision"), "exif": result.get("exif"),
                        "pHash": result.get("pHash"),
                        "processingCopy": result.get("processingCopy")})
        elif item["group"] == "slip":
            out.append({"evidenceId": item["evidenceId"], "filename": item.get("filename"),
                        "kind": "textract", "fields": result.get("fields"),
                        "confidenceAvg": result.get("confidenceAvg"),
                        "missingFields": result.get("missingFields"),
                        "lowConfidenceFields": result.get("lowConfidenceFields"),
                        "mocked": result.get("mocked")})
        elif item["group"] == "trace":
            out.append({"evidenceId": item["evidenceId"], "filename": item.get("filename"),
                        "kind": "trace", **{k: result.get(k) for k in (
                            "vehicleNo", "pointCount", "startTime", "endTime", "distanceM",
                            "maxGapSeconds", "route", "warnings")}})
        elif item["group"] == "bill":
            out.append({"evidenceId": item["evidenceId"], "filename": item.get("filename"),
                        "kind": "bill", **result})
    return out


def summary_text(checks, counts, *, mocked):
    """A fixed template over the checks: it can only repeat what they say."""
    total = len(checks)
    problems = [check for check in checks if check["status"] in (FAIL, REVIEW)]
    lines = []
    if mocked:
        lines.append("OFFLINE MOCK: the photo and slip readings below came from test fixtures, "
                     "not from Amazon Bedrock or Textract.")
    lines.append(
        f"{total} checks: {counts[FAIL]} failed, {counts[REVIEW]} need review, "
        f"{counts[PASS]} passed, {counts[CONSISTENT]} consistent with an approximate reference, "
        f"{counts[INCONCLUSIVE]} inconclusive, and {counts[NOT_EVALUATED]} not evaluated for "
        "lack of evidence."
    )
    if problems:
        lines.append("Problems: " + " ".join(
            f"{check['id']}: {check['message']}" for check in problems[:3]
        ) + (f" (+{len(problems) - 3} more)" if len(problems) > 3 else ""))
    else:
        lines.append("No check found a problem with the evidence that was supplied.")
    missing = sorted({need for check in checks if check["status"] == NOT_EVALUATED
                      for need in check["missing"]})
    if missing:
        lines.append("Supplying " + "; ".join(missing[:5])
                     + (" and more" if len(missing) > 5 else "")
                     + " would let more checks run.")
    lines.append("Not evaluated is not passed.")
    return " ".join(lines)
