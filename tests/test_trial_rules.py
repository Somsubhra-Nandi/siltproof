"""Trial checks and validators as pure functions: no AWS, no moto.

These pin the honesty rules: missing evidence is NOT_EVALUATED, an
approximate reference never yields PASS for R1, unreadable values are
INCONCLUSIVE, and the B1 rule thresholds are the ones reused.
"""

import datetime
import json
import pathlib

import pytest

from common import config, rules
from trial import analysis, validate

REPO = pathlib.Path(__file__).resolve().parent.parent
DRAIN = {"point": [88.4712, 22.5801], "toleranceM": 30, "source": "manual_point",
         "derivedFromPhotos": False}


def photo(evidence_id="ev_aaaaaaaaaaaaaaaaaaaa", *, lat=22.5801, lon=88.4712, role="current",
          stamp="2026-10-09T09:53:12", phash="ffffffff00000000", vision=None, accuracy=None):
    return {
        "evidenceId": evidence_id, "group": "photo", "role": role, "filename": f"{evidence_id}.jpg",
        "result": {
            "exif": {"hasGps": lat is not None, "lat": lat, "lon": lon, "timestamp": stamp,
                     "timestampHasOffset": False, "gpsAccuracyM": accuracy},
            "pHash": phash,
            "vision": vision or {"ran": True, "ok": True, "cleared": True, "confidence": 0.9,
                                 "mocked": False, "loadType": "silt"},
        },
    }


def slip(evidence_id="ev_ssssssssssssssssssss", *, net=9.3, gross=18.4, tare=9.1,
         time_in="07:24", vehicle="WB 00 MK 0001", mocked=False, derived=False):
    def field(value):
        return {"value": value, "ok": value is not None, "confidence": 97.0}

    fields = {"net": field(net), "gross": field(gross), "tare": field(tare),
              "timeIn": field(time_in), "vehicleNo": field(vehicle)}
    if derived:
        fields["net"]["derived"] = "gross-tare"
    return {"evidenceId": evidence_id, "group": "slip", "filename": "slip.jpg",
            "result": {"fields": fields, "mocked": mocked}}


def trace(evidence_id="ev_tttttttttttttttttttt", vehicle="WB 00 MK 0001"):
    return {"evidenceId": evidence_id, "group": "trace", "filename": "trace.json",
            "result": {"vehicleNo": vehicle}}


def points(start=(88.4712, 22.5801), end=(88.43, 22.56), t0="2026-10-09T07:00:00+05:30",
           minutes=20, steps=10):
    base = datetime.datetime.fromisoformat(t0)
    out = []
    for index in range(steps + 1):
        fraction = index / steps
        out.append({"lon": start[0] + (end[0] - start[0]) * fraction,
                    "lat": start[1] + (end[1] - start[1]) * fraction,
                    "t": (base + datetime.timedelta(minutes=minutes * fraction)).isoformat()})
    return out


SITE = {"point": [88.43, 22.56], "radiusM": 200}


# --------------------------------------------------------------------- R1
def test_r1_inside_tolerance_is_consistent_never_pass():
    check = analysis.check_r1(photo(lat=22.58015), DRAIN)
    assert check["status"] == "CONSISTENT" and check["basis"] == "approximate"
    assert "not an official drain map" in check["message"]


def test_r1_uses_b1s_soft_margin_beyond_the_tolerance():
    assert analysis.R1_MARGIN_M == config.R1_SOFT_BAND_M - config.DRAIN_BUFFER_M == 30
    review = analysis.check_r1(photo(lat=22.5801 + 45 / 111_320), DRAIN)
    assert review["status"] == "REVIEW" and review["severity"] == rules.SOFT
    fail = analysis.check_r1(photo(lat=22.5801 + 80 / 111_320), DRAIN)
    assert fail["status"] == "FAIL" and fail["severity"] == rules.HARD


def test_r1_far_away_photo_fails_like_b1_would():
    check = analysis.check_r1(photo(lat=19.076, lon=72.8777), DRAIN)   # Mumbai
    assert check["status"] == "FAIL"
    assert check["evidence"]["distanceM"] > 1_000_000


def test_r1_without_gps_or_drain_is_not_evaluated():
    assert analysis.check_r1(photo(lat=None, lon=None), DRAIN)["status"] == "NOT_EVALUATED"
    check = analysis.check_r1(photo(), None)
    assert check["status"] == "NOT_EVALUATED" and check["missing"] == ["a drain location"]


def test_r1_circular_geometry_and_poor_accuracy_are_inconclusive():
    circular = analysis.check_r1(photo(), {**DRAIN, "derivedFromPhotos": True})
    assert circular["status"] == "INCONCLUSIVE"
    vague = analysis.check_r1(photo(accuracy=65), DRAIN)
    assert vague["status"] == "INCONCLUSIVE" and "accuracy" in vague["message"]


def test_r1_measures_to_a_line_when_one_is_given():
    drain = {**DRAIN, "line": [[88.4700, 22.5801], [88.4800, 22.5801]], "point": [88.475, 22.5801]}
    check = analysis.check_r1(photo(lon=88.4712), drain)
    assert check["evidence"]["reference"] == "line" and check["evidence"]["distanceM"] < 1


# --------------------------------------------------------------------- R2
WINDOW = {"start": "2026-10-09T00:00:00+05:30", "end": "2026-10-09T23:59:59+05:30"}


def test_r2_inside_and_outside_the_window():
    assert analysis.check_r2(photo(), WINDOW)["status"] == "PASS"
    late = analysis.check_r2(photo(stamp="2026-10-10T09:00:00"), WINDOW)
    assert late["status"] == "FAIL" and late["severity"] == rules.HARD


def test_r2_says_when_it_assumed_a_timezone():
    check = analysis.check_r2(photo(), WINDOW)
    assert check["evidence"]["timezoneAssumed"] is True
    assert "no timezone" in check["message"]


def test_r2_needs_both_a_time_and_a_window():
    assert analysis.check_r2(photo(stamp=None), WINDOW)["status"] == "NOT_EVALUATED"
    assert analysis.check_r2(photo(), None)["missing"] == ["a work window"]


# --------------------------------------------------------------------- R3
def test_r3_needs_two_photos():
    checks = analysis.check_r3([photo()])
    assert len(checks) == 1 and checks[0]["status"] == "NOT_EVALUATED"


def test_r3_flags_a_reused_image_within_the_trial():
    a = photo("ev_aaaaaaaaaaaaaaaaaaaa", phash="ffffffff00000000", stamp="2026-10-09T09:00:00")
    b = photo("ev_bbbbbbbbbbbbbbbbbbbb", phash="ffffffff00000003", stamp="2026-10-09T10:00:00")
    c = photo("ev_cccccccccccccccccccc", phash="0000000000ffff0f", stamp="2026-10-09T11:00:00")
    checks = {check["subject"]["evidenceId"]: check for check in analysis.check_r3([a, b, c])}
    assert checks["ev_bbbbbbbbbbbbbbbbbbbb"]["status"] == "FAIL"
    assert checks["ev_bbbbbbbbbbbbbbbbbbbb"]["evidence"]["hammingDistance"] == 2
    assert checks["ev_aaaaaaaaaaaaaaaaaaaa"]["status"] == "PASS"
    assert checks["ev_cccccccccccccccccccc"]["status"] == "PASS"


# --------------------------------------------------------------------- R4
def test_r4_only_judges_photos_labelled_after():
    checks = analysis.check_r4([photo(role="before"), photo(role="current")])
    assert checks[0]["status"] == "NOT_EVALUATED"
    assert "labelled 'after'" in checks[0]["missing"][0]


def test_r4_reports_the_model_as_it_answered():
    not_cleared = photo(role="after", vision={"ran": True, "ok": True, "cleared": False,
                                              "confidence": 0.7, "mocked": False})
    assert analysis.check_r4([not_cleared])[0]["status"] == "REVIEW"
    malformed = photo(role="after", vision={"ran": True, "ok": False, "cleared": False,
                                            "mocked": False})
    assert analysis.check_r4([malformed])[0]["status"] == "INCONCLUSIVE"
    skipped = photo(role="after", vision={"ran": False, "skippedReason": "QUOTA_EXHAUSTED"})
    check = analysis.check_r4([skipped])[0]
    assert check["status"] == "NOT_EVALUATED" and "QUOTA_EXHAUSTED" in check["missing"][0]
    cleared = analysis.check_r4([photo(role="after")])[0]
    assert cleared["status"] == "PASS" and "not proof" in cleared["message"]


def test_mocked_outcomes_are_demoted_to_not_evaluated():
    mocked = photo(role="after", vision={"ran": True, "ok": True, "cleared": True,
                                         "confidence": 0.9, "mocked": True})
    check = analysis.demote_mocked(analysis.check_r4([mocked])[0])
    assert check["status"] == "NOT_EVALUATED" and check["mockOutcome"] == "PASS"
    assert check["message"].startswith("OFFLINE MOCK")


# ---------------------------------------------------------------- traces
def test_r5_and_gps_gap_on_a_good_trace():
    checks = {check["id"]: check for check in analysis.check_trace(trace(), points(), SITE)}
    assert checks["R5"]["status"] == "PASS"
    assert "not make it an authorised landfill" in checks["R5"]["message"]
    assert checks["GPS_GAP"]["status"] == "PASS"


def test_r5_fails_when_the_trace_never_reaches_the_site():
    elsewhere = points(end=(88.50, 22.60))
    check = analysis.check_trace(trace(), elsewhere, SITE)[0]
    assert check["status"] == "FAIL" and check["severity"] == rules.HARD


def test_r5_without_a_site_is_not_evaluated_and_gaps_are_flagged():
    gappy = points(minutes=60, steps=3)
    checks = {check["id"]: check for check in analysis.check_trace(trace(), gappy, None)}
    assert checks["R5"]["status"] == "NOT_EVALUATED"
    assert checks["GPS_GAP"]["status"] == "REVIEW"


def test_polygon_site_is_used_when_given():
    ring = [[88.425, 22.555], [88.435, 22.555], [88.435, 22.565], [88.425, 22.565],
            [88.425, 22.555]]
    geofence = analysis.disposal_geofence({"point": [88.43, 22.56], "polygon": ring})
    assert geofence["coordinates"] == [ring]


def test_r6_needs_two_traces_of_one_truck_and_catches_teleports():
    a, b = trace("ev_t1aaaaaaaaaaaaaaaaa"), trace("ev_t2aaaaaaaaaaaaaaaaa")
    assert analysis.check_r6([a], {a["evidenceId"]: points()}, None)[0]["status"] == \
        "NOT_EVALUATED"
    first = points(t0="2026-10-09T07:00:00+05:30", minutes=10)
    second = points(start=(88.60, 22.70), end=(88.61, 22.71),
                    t0="2026-10-09T07:12:00+05:30", minutes=10)
    checks = analysis.check_r6([a, b], {a["evidenceId"]: first, b["evidenceId"]: second}, None)
    assert {check["status"] for check in checks} == {"FAIL"}


# ------------------------------------------------------------------ slips
def test_slip_checks_with_a_paired_trace():
    checks = {c["id"]: c for c in analysis.check_slip(
        slip(time_in="07:21"), trace(), points(), SITE, {"capacityTonnes": 10})}
    assert checks["R7"]["status"] == "PASS"
    assert checks["R8"]["status"] == "PASS"
    assert checks["R9"]["status"] == "PASS"
    assert checks["T2"]["status"] == "PASS"


def test_slip_failures_reuse_b1_thresholds_and_wording():
    checks = {c["id"]: c for c in analysis.check_slip(
        slip(net=12.0, gross=22.0, tare=9.0, time_in="06:30", vehicle="WB 99 ZZ 9999"),
        trace(), points(), SITE, {"capacityTonnes": 10})}
    assert checks["R7"]["status"] == "FAIL"
    assert checks["R8"]["status"] == "FAIL" and "minutes" in checks["R8"]["message"]
    assert checks["R9"]["status"] == "FAIL"
    assert checks["T2"]["status"] == "FAIL"


def test_slip_without_partners_is_not_evaluated_or_inconclusive():
    checks = {c["id"]: c for c in analysis.check_slip(
        slip(net=None, time_in=None, vehicle=None), None, None, None, None)}
    assert checks["R7"]["status"] == "INCONCLUSIVE"
    assert checks["R8"]["status"] == "INCONCLUSIVE"
    assert checks["R9"]["status"] == "INCONCLUSIVE"
    assert checks["T2"]["status"] == "INCONCLUSIVE"
    checks = {c["id"]: c for c in analysis.check_slip(slip(), None, None, None, None)}
    assert checks["R7"]["status"] == "NOT_EVALUATED"
    assert checks["R8"]["status"] == "NOT_EVALUATED" and len(checks["R8"]["missing"]) == 2
    assert checks["R9"]["status"] == "NOT_EVALUATED"


def test_t2_refuses_to_check_a_derived_net():
    check = [c for c in analysis.check_slip(slip(derived=True), None, None, None, None)
             if c["id"] == "T2"][0]
    assert check["status"] == "INCONCLUSIVE" and "circular" in check["message"]


def test_pairing_by_vehicle_number():
    slips = [slip("ev_s1aaaaaaaaaaaaaaaaa", vehicle="WB 01 AA 0001"),
             slip("ev_s2aaaaaaaaaaaaaaaaa", vehicle="WB 02 BB 0002")]
    traces = [trace("ev_t1aaaaaaaaaaaaaaaaa", vehicle="WB 02 BB 0002"),
              trace("ev_t2aaaaaaaaaaaaaaaaa", vehicle="wb-01-aa-0001")]
    assert analysis.pair_slips(slips, traces, None) == {
        "ev_s1aaaaaaaaaaaaaaaaa": "ev_t2aaaaaaaaaaaaaaaaa",
        "ev_s2aaaaaaaaaaaaaaaaa": "ev_t1aaaaaaaaaaaaaaaaa",
    }


# ------------------------------------------------------------------ claim
def test_claim_arithmetic_and_volume():
    checks = {c["id"]: c for c in analysis.check_claim(
        {"quantityTonnes": 50, "ratePerTonne": 1800, "amountRupees": 90000},
        {"lengthM": 100, "widthM": 2, "depthM": 1}, [])}
    assert checks["T1"]["status"] == "PASS"
    assert checks["R10"]["status"] == "PASS"
    assert checks["T3"]["status"] == "NOT_EVALUATED"

    checks = {c["id"]: c for c in analysis.check_claim(
        {"quantityTonnes": 500, "ratePerTonne": 1800, "amountRupees": 950000},
        {"lengthM": 100, "widthM": 2, "depthM": 1}, [slip()])}
    assert checks["T1"]["status"] == "FAIL" and "₹900,000.00" in checks["T1"]["message"]
    assert checks["R10"]["status"] == "REVIEW"
    assert checks["T3"]["status"] == "REVIEW" and "9.3 t of the 500 t" in checks["T3"]["message"]


def test_partial_claim_is_not_evaluated():
    checks = {c["id"]: c for c in analysis.check_claim({"quantityTonnes": 10}, None, [])}
    assert checks["T1"]["status"] == "NOT_EVALUATED"
    assert set(checks["T1"]["missing"]) == {"the claimed rate", "the claimed amount"}
    assert checks["R10"]["status"] == "NOT_EVALUATED"


# --------------------------------------------------------------- evaluate
def test_evaluate_with_nothing_evaluates_nothing():
    checks, counts = analysis.evaluate({"details": {}}, [], {})
    assert counts["NOT_EVALUATED"] == len(checks) and counts["PASS"] == 0
    assert {check["id"] for check in checks} == set(analysis.TITLES)


def test_summary_only_repeats_the_checks():
    checks, counts = analysis.evaluate({"details": {"drainLocation": DRAIN}},
                                       [photo(lat=19.0, lon=72.8)], {})
    text = analysis.summary_text(checks, counts, mocked=False)
    assert "1 failed" in text and "R1:" in text and "Not evaluated is not passed." in text
    assert not text.startswith("OFFLINE MOCK")


def test_b1_rules_module_is_not_modified_by_the_trial_feature():
    """The adaptor imports R1-R10; it must not have grown trial logic."""
    source = (REPO / "backend" / "common" / "rules.py").read_text()
    assert "trial" not in source.lower()
    assert rules.PHASH_DUPLICATE_MAX == 12 and rules.SLIP_TOLERANCE_MIN == 10.0


# --------------------------------------------------------------- validate
@pytest.mark.parametrize("point", [[0, 0], [200, 10], [10, 95], ["88", "22"], [88], None])
def test_bad_points_are_refused(point):
    with pytest.raises(ValueError):
        validate.lonlat(point)


def test_details_validation_reports_each_field():
    updates, clears, errors = validate.details({
        "drainLocation": {"point": [88.47, 22.58], "toleranceM": 1000},
        "disposalSite": {"point": [88.43, 22.56], "radiusM": 5},
        "claim": {"ratePerTonne": "1800"},
        "truck": {},
        "workWindow": {"start": "2026-10-09T00:00:00", "end": "2026-10-09T10:00:00+05:30"},
    })
    assert set(errors) == {"drainLocation.toleranceM", "disposalSite.radiusM",
                           "claim.ratePerTonne", "claim.quantityTonnes", "truck.vehicleNo",
                           "workWindow.end"}
    assert updates == {} and clears == []


def test_details_validation_accepts_a_line_and_derives_a_pin():
    updates, _, errors = validate.details({"drainLocation": {
        "line": [[88.47, 22.58], [88.471, 22.581], [88.472, 22.582]],
        "source": "field_approximate", "derivedFromPhotos": True}})
    assert errors == {}
    assert updates["drainLocation"]["point"] == [88.471, 22.581]


def test_vehicle_numbers_are_normalised():
    assert validate.normalise_vehicle("wb-25 ab.1234") == "WB 25 AB 1234"


def test_signatures():
    assert validate.signature_ok("image/jpeg", b"\xff\xd8\xff\xe1rest")
    assert not validate.signature_ok("image/jpeg", b"\x89PNG\r\n\x1a\n")
    assert validate.signature_ok("application/pdf", b"%PDF-1.7")
    assert validate.signature_ok("application/json", b"\xef\xbb\xbf  {\"points\": []}")
    assert not validate.signature_ok("application/json", b"lat,lon,t")
    assert not validate.signature_ok("image/gif", b"GIF89a")


def _trace(pts, **extra):
    return json.dumps({"points": pts, **extra}).encode()


GOOD = [{"lat": 22.58, "lon": 88.47, "t": "2026-10-09T07:00:00+05:30"},
        {"lat": 22.57, "lon": 88.46, "t": "2026-10-09T07:05:00+05:30"}]


@pytest.mark.parametrize("payload, reason", [
    (b"not json", "not valid UTF-8 JSON"),
    (_trace(GOOD[:1]), "at least 2 points"),
    (_trace([{**GOOD[0], "lat": 95}, GOOD[1]]), "latitude 95 is out of range"),
    (_trace([{**GOOD[0], "lon": None}, GOOD[1]]), "longitude is missing"),
    (_trace([{**GOOD[0], "lat": 0, "lon": 0}, GOOD[1]]), "[0, 0]"),
    (_trace([{**GOOD[0], "t": "yesterday"}, GOOD[1]]), "not ISO 8601"),
    (_trace([{**GOOD[0], "t": None}, GOOD[1]]), "no timestamp"),
    (_trace([GOOD[1], GOOD[0]]), "backwards"),
    (_trace([{**GOOD[0], "t": "2026-10-09T07:00:00"}, GOOD[1]]), "mix"),
    (json.dumps([[88.47, 22.58]]).encode(), "unrecognised trace format"),
    (json.dumps({"type": "Feature", "properties": {"times": ["2026-10-09T07:00:00Z"]},
                 "geometry": {"type": "LineString",
                              "coordinates": [[88.47, 22.58], [88.46, 22.57]]}}).encode(),
     "one timestamp per coordinate"),
    (json.dumps({"type": "Feature", "properties": {},
                 "geometry": {"type": "Point", "coordinates": [88.47, 22.58]}}).encode(),
     "LineString"),
])
def test_invalid_traces_are_refused_with_a_reason(payload, reason):
    with pytest.raises(validate.TraceError) as caught:
        validate.parse_trace(payload)
    assert reason in str(caught.value)


def test_trace_warnings_do_not_reject():
    pts = [{"lat": 22.58, "lon": 88.47, "t": "2026-10-09T07:00:00"},
           {"lat": 22.58, "lon": 88.47, "t": "2026-10-09T07:01:00"},
           {"lat": 23.58, "lon": 88.47, "t": "2026-10-09T07:02:00"}]
    trace_ = validate.parse_trace(_trace(pts, vehicleNo="wb 1"))
    assert trace_["pointCount"] == 3 and trace_["vehicleNo"] == "WB 1"
    text = " ".join(trace_["warnings"])
    assert "no timezone" in text and "repeat" in text and "km/h" in text


def test_swapped_coordinates_are_spotted():
    swapped = [{"lon": 22.5801, "lat": 88.4712}]
    assert validate.swapped_hint(swapped, [88.4712, 22.5801])
    assert not validate.swapped_hint([{"lon": 88.47, "lat": 22.58}], [88.4712, 22.5801])


def test_pdf_page_count():
    from trial_support import minimal_pdf

    assert validate.pdf_page_count(minimal_pdf(1)) == 1
    assert validate.pdf_page_count(minimal_pdf(4)) == 4


# ------------------------------------------------------------ infrastructure
def test_template_wires_trial_cleanup_quotas_and_routes():
    text = (REPO / "infra" / "template.yaml").read_text()
    assert "Prefix: trials/" in text and "ExpirationInDays: 2" in text
    assert "TimeToLiveSpecification" in text and "AttributeName: ttl" in text
    assert "TRIAL_PROCESSOR_FUNCTION: !Ref IngestFunction" in text
    assert "LambdaInvokePolicy" in text
    assert "AllowHeaders: [Content-Type, Authorization]" in text
    for route in ("Path: /trials\n", "Path: /trials/{trialId}/upload-url",
                  "Path: /trials/{trialId}/evidence/{evidenceId}/complete",
                  "Path: /trials/{trialId}/analyze", "Path: /trials/{trialId}/results"):
        assert route in text
    # The S3 notifications that trigger B1 ingestion still exclude trials/.
    for prefix in ("photos/", "slips/", "traces/"):
        assert f"Value: {prefix}" in text
    assert "Value: trials/" not in text
