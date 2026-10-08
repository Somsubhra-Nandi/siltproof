"""Each rule on its own, with hand-built evidence and the awkward cases.

The oracle test proves the rules agree with the dataset. These prove each rule
fires for the reason it claims to, and that absent or unreadable evidence is
never silently treated as a pass.
"""

import pytest

from common import rules

# A 200 m x 60 m rectangle, with its centreline, standing in for a drain.
DRAIN = {
    "drainId": "1",
    "name": "Test drain",
    "geofence": {
        "type": "Polygon",
        "coordinates": [[
            [72.8700, 19.0700], [72.8719, 19.0700],
            [72.8719, 19.0705], [72.8700, 19.0705], [72.8700, 19.0700],
        ]],
    },
    "centreline": [[72.8700, 19.07025], [72.8719, 19.07025]],
    "plausibleMaxTonnes": 500,
}

DUMPSITE = {
    "geofence": {
        "type": "Polygon",
        "coordinates": [[
            [72.9300, 19.1250], [72.9340, 19.1250],
            [72.9340, 19.1290], [72.9300, 19.1290], [72.9300, 19.1250],
        ]],
    },
    "center": [72.9320, 19.1270],
}

WINDOW = ("2026-09-21T06:00:00+05:30", "2026-10-04T19:00:00+05:30")


def photo(**overrides):
    item = {
        "s3Key": "photos/B1/drain1/after-01.jpg",
        "drainId": "1",
        "role": "after",
        "status": "OK",
        "hasGps": True,
        "lat": 19.07025,
        "lon": 72.8710,
        "timestamp": "2026-09-25T10:00:00+05:30",
        "pHash": "aaaabbbbccccdddd",
        "bedrock": {"cleared": True, "load_type": "unclear", "confidence": 0.9,
                    "notes": "", "ok": True},
        "problems": [],
    }
    item.update(overrides)
    return item


def trip(**overrides):
    item = {
        "tripId": "1#001", "drainId": "1", "tripNo": "001",
        "vehicleNo": "MH 01 AA 1000", "claimedTonnes": 9.0,
        "slipKey": "slips/B1/1-001.png", "traceKey": "traces/B1/1-001.json",
    }
    item.update(overrides)
    return item


def slip(**overrides):
    item = {
        "s3Key": "slips/B1/1-001.png", "status": "OK",
        "vehicleNo": "MH 01 AA 1000", "net": 9.0, "gross": 21.6, "tare": 12.6,
        "timeIn": "10:30", "timeOut": "10:50",
        "textract": {"confidenceAvg": 97.0, "missingFields": []},
    }
    item.update(overrides)
    return item


def trace_to_dump(start="2026-09-25T10:00:00+05:30", arrive_at_dump=True, step_s=30):
    """A trace from the drain to the dump site, one point every 30 s."""
    import datetime

    begin = datetime.datetime.fromisoformat(start)
    finish = [72.9320, 19.1270] if arrive_at_dump else [72.9100, 19.1100]
    points = []

    for index in range(41):
        fraction = index / 40
        points.append(
            {
                "t": (begin + datetime.timedelta(seconds=index * step_s)).isoformat(),
                "lon": 72.8710 + (finish[0] - 72.8710) * fraction,
                "lat": 19.07025 + (finish[1] - 19.07025) * fraction,
            }
        )
    return points


def rule_ids(findings, severity=None):
    return sorted(
        item["rule"] for item in findings
        if severity is None or item["severity"] == severity
    )


# ------------------------------------------------------------------ R1-R4
def test_r1_passes_inside_the_geofence():
    findings = rules.check_photos(DRAIN, [photo()], {}, WINDOW)
    assert "R1" not in rule_ids(findings)


def test_r1_is_soft_in_the_30_to_60_metre_band():
    """A consumer GPS fix is routinely tens of metres out."""
    just_outside = photo(lat=19.07065)        # about 44 m from the centreline
    findings = rules.check_photos(DRAIN, [just_outside], {}, WINDOW)

    r1 = [item for item in findings if item["rule"] == "R1"]
    assert len(r1) == 1
    assert r1[0]["severity"] == "soft"
    assert 30 < r1[0]["evidence"]["distanceM"] <= 60


def test_r1_is_hard_beyond_the_band():
    far = photo(lat=19.0730)                  # about 300 m away
    findings = rules.check_photos(DRAIN, [far], {}, WINDOW)

    r1 = [item for item in findings if item["rule"] == "R1"]
    assert r1[0]["severity"] == "hard"
    assert r1[0]["evidence"]["distanceM"] > 60


def test_r1_without_gps_is_soft_not_a_pass():
    findings = rules.check_photos(
        DRAIN, [photo(hasGps=False, lat=None, lon=None)], {}, WINDOW
    )
    r1 = [item for item in findings if item["rule"] == "R1"]

    assert r1[0]["severity"] == "soft"
    assert "no GPS" in r1[0]["message"]


def test_r2_catches_a_photo_from_outside_the_work_window():
    findings = rules.check_photos(
        DRAIN, [photo(timestamp="2026-08-01T10:00:00+05:30")], {}, WINDOW
    )
    r2 = [item for item in findings if item["rule"] == "R2"]

    assert r2[0]["severity"] == "hard"


def test_r2_compares_naive_and_aware_timestamps_safely():
    findings = rules.check_photos(
        DRAIN, [photo(timestamp="2026-09-25T10:00:00")], {}, WINDOW
    )
    assert "R2" not in rule_ids(findings)


def test_r2_without_a_timestamp_is_soft():
    findings = rules.check_photos(DRAIN, [photo(timestamp=None)], {}, WINDOW)
    r2 = [item for item in findings if item["rule"] == "R2"]

    assert r2[0]["severity"] == "soft"


def test_r3_flags_the_later_copy_and_clears_the_original():
    original = photo(s3Key="photos/B1/drain9/after-01.jpg", drainId="9",
                     timestamp="2026-09-25T10:00:00+05:30")
    copy = photo(s3Key="photos/B1/drain14/after-02.jpg", drainId="14",
                 timestamp="2026-09-26T10:00:00+05:30")

    duplicates = rules.find_duplicates([original, copy])

    assert copy["s3Key"] in duplicates
    assert original["s3Key"] not in duplicates
    assert duplicates[copy["s3Key"]]["drainId"] == "9"


def test_r3_ignores_photos_that_merely_look_similar():
    a = photo(s3Key="a.jpg", pHash="0000000000000000")
    b = photo(s3Key="b.jpg", pHash="ffffffffffffffff", timestamp="2026-09-26T10:00:00+05:30")

    assert rules.find_duplicates([a, b]) == {}


def test_r3_is_hard_when_a_duplicate_is_found():
    duplicates = {
        "photos/B1/drain1/after-01.jpg": {
            "original": "photos/B1/drain9/after-01.jpg", "drainId": "9", "distance": 4
        }
    }
    findings = rules.check_photos(DRAIN, [photo()], duplicates, WINDOW)
    r3 = [item for item in findings if item["rule"] == "R3"]

    assert r3[0]["severity"] == "hard"
    assert "drain 9" in r3[0]["message"]


def test_r4_is_hard_for_debris_and_silent_for_silt():
    debris = photo(role="load", bedrock={"cleared": True, "load_type": "debris",
                                         "confidence": 0.9, "notes": "rubble", "ok": True})
    silt = photo(role="load", s3Key="photos/B1/drain1/load-02.jpg",
                 bedrock={"cleared": True, "load_type": "silt",
                          "confidence": 0.9, "notes": "", "ok": True})

    hard = rules.check_photos(DRAIN, [debris], {}, WINDOW)
    clean = rules.check_photos(DRAIN, [silt], {}, WINDOW)

    assert [item["severity"] for item in hard if item["rule"] == "R4"] == ["hard"]
    assert "R4" not in rule_ids(clean)


def test_r4_is_soft_when_the_load_cannot_be_identified():
    unclear = photo(role="load", bedrock={"cleared": True, "load_type": "unclear",
                                          "confidence": 0.3, "notes": "", "ok": True})
    findings = rules.check_photos(DRAIN, [unclear], {}, WINDOW)

    assert [item["severity"] for item in findings if item["rule"] == "R4"] == ["soft"]


def test_r4_is_soft_when_the_after_photo_does_not_look_cleared():
    findings = rules.check_photos(
        DRAIN,
        [photo(bedrock={"cleared": False, "load_type": "unclear",
                        "confidence": 0.8, "notes": "", "ok": True})],
        {}, WINDOW,
    )
    assert [item["severity"] for item in findings if item["rule"] == "R4"] == ["soft"]


def test_a_before_photo_is_not_expected_to_look_cleared():
    findings = rules.check_photos(
        DRAIN,
        [photo(role="before", bedrock={"cleared": False, "load_type": "unclear",
                                       "confidence": 0.9, "notes": "", "ok": True})],
        {}, WINDOW,
    )
    assert "R4" not in rule_ids(findings)


def test_a_drain_with_no_photos_is_flagged():
    findings = rules.check_photos(DRAIN, [], {}, WINDOW)

    assert rule_ids(findings) == ["PHOTOS_MISSING"]
    assert findings[0]["severity"] == "soft"


def test_a_photo_that_failed_ingestion_is_flagged():
    findings = rules.check_photos(
        DRAIN, [photo(status="ERROR", error="NoSuchKey")], {}, WINDOW
    )
    assert rule_ids(findings) == ["EVIDENCE_ERROR"]


# ------------------------------------------------------------------ R5-R9
def test_r5_passes_when_the_trace_reaches_the_dump_site():
    findings = rules.check_trip(
        trip(), slip(), trace_to_dump(), {"status": "OK"}, DUMPSITE,
        {"MH 01 AA 1000": 16}, {},
    )
    assert "R5" not in rule_ids(findings)


def test_r5_is_hard_when_the_truck_never_arrives():
    points = trace_to_dump(arrive_at_dump=False)
    findings = rules.check_trip(
        trip(), slip(timeIn="10:20"), points, {"status": "OK"}, DUMPSITE,
        {"MH 01 AA 1000": 16}, {},
    )
    r5 = [item for item in findings if item["rule"] == "R5"]

    assert r5[0]["severity"] == "hard"


def test_r6_flags_both_trips_of_an_impossible_pair():
    import datetime

    first = trip(tripId="16#002", traceKey="a")
    second = trip(tripId="16#006", traceKey="b")

    begin = datetime.datetime.fromisoformat("2026-09-25T10:00:00+05:30")
    traces = {
        "a": [{"t": begin.isoformat(), "lon": 72.87, "lat": 19.07},
              {"t": (begin + datetime.timedelta(minutes=30)).isoformat(),
               "lon": 72.93, "lat": 19.12}],
        "b": [{"t": (begin + datetime.timedelta(minutes=37)).isoformat(),
               "lon": 73.14, "lat": 19.12},
              {"t": (begin + datetime.timedelta(minutes=60)).isoformat(),
               "lon": 73.15, "lat": 19.13}],
    }

    pairs = rules.find_impossible_pairs([first, second], traces)

    assert set(pairs) == {"16#002", "16#006"}
    assert pairs["16#002"][0]["impliedSpeedKmh"] > 40


def test_r6_accepts_a_realistic_turnaround():
    import datetime

    begin = datetime.datetime.fromisoformat("2026-09-25T10:00:00+05:30")
    traces = {
        "a": [{"t": begin.isoformat(), "lon": 72.87, "lat": 19.07},
              {"t": (begin + datetime.timedelta(minutes=30)).isoformat(),
               "lon": 72.93, "lat": 19.12}],
        "b": [{"t": (begin + datetime.timedelta(minutes=90)).isoformat(),
               "lon": 72.87, "lat": 19.07},
              {"t": (begin + datetime.timedelta(minutes=120)).isoformat(),
               "lon": 72.93, "lat": 19.12}],
    }

    assert rules.find_impossible_pairs(
        [trip(tripId="a", traceKey="a"), trip(tripId="b", traceKey="b")], traces
    ) == {}


def test_r6_treats_overlapping_trips_as_impossible():
    """One truck cannot start a second trip before finishing the first."""
    import datetime

    begin = datetime.datetime.fromisoformat("2026-09-25T10:00:00+05:30")
    traces = {
        "a": [{"t": begin.isoformat(), "lon": 72.87, "lat": 19.07},
              {"t": (begin + datetime.timedelta(minutes=40)).isoformat(),
               "lon": 72.93, "lat": 19.12}],
        "b": [{"t": (begin + datetime.timedelta(minutes=20)).isoformat(),
               "lon": 72.87, "lat": 19.07},
              {"t": (begin + datetime.timedelta(minutes=60)).isoformat(),
               "lon": 72.93, "lat": 19.12}],
    }

    pairs = rules.find_impossible_pairs(
        [trip(tripId="a", traceKey="a"), trip(tripId="b", traceKey="b")], traces
    )
    assert set(pairs) == {"a", "b"}


def test_r7_is_hard_when_the_load_beats_the_truck():
    findings = rules.check_trip(
        trip(), slip(net=14.0), trace_to_dump(), {"status": "OK"}, DUMPSITE,
        {"MH 01 AA 1000": 10}, {},
    )
    r7 = [item for item in findings if item["rule"] == "R7"]

    assert r7[0]["severity"] == "hard"
    assert r7[0]["evidence"]["capacityTonnes"] == 10


def test_r7_cannot_fire_without_a_capacity_record():
    findings = rules.check_trip(
        trip(), slip(net=14.0), trace_to_dump(), {"status": "OK"}, DUMPSITE, {}, {}
    )
    assert "R7" not in rule_ids(findings)


def test_r7_is_soft_when_the_net_weight_is_unreadable():
    findings = rules.check_trip(
        trip(), slip(net=None), trace_to_dump(), {"status": "OK"}, DUMPSITE,
        {"MH 01 AA 1000": 16}, {},
    )
    assert [item["severity"] for item in findings if item["rule"] == "R7"] == ["soft"]


def test_r8_passes_within_ten_minutes():
    points = trace_to_dump(start="2026-09-25T10:00:00+05:30")
    findings = rules.check_trip(
        trip(), slip(timeIn="10:15"), points, {"status": "OK"}, DUMPSITE,
        {"MH 01 AA 1000": 16}, {},
    )
    assert "R8" not in rule_ids(findings)


def test_r8_catches_a_slip_printed_before_the_truck_arrived():
    points = trace_to_dump(start="2026-09-25T10:00:00+05:30")
    findings = rules.check_trip(
        trip(), slip(timeIn="09:30"), points, {"status": "OK"}, DUMPSITE,
        {"MH 01 AA 1000": 16}, {},
    )
    r8 = [item for item in findings if item["rule"] == "R8"]

    assert r8[0]["severity"] == "hard"
    assert r8[0]["evidence"]["minutesEarly"] > 10


def test_r8_handles_a_slip_printed_either_side_of_midnight():
    points = trace_to_dump(start="2026-09-25T23:50:00+05:30")
    findings = rules.check_trip(
        trip(), slip(timeIn="00:05"), points, {"status": "OK"}, DUMPSITE,
        {"MH 01 AA 1000": 16}, {},
    )
    assert "R8" not in rule_ids(findings)


def test_r9_catches_a_slip_for_a_different_truck():
    findings = rules.check_trip(
        trip(), slip(vehicleNo="MH 09 ZZ 9999"), trace_to_dump(), {"status": "OK"},
        DUMPSITE, {"MH 01 AA 1000": 16}, {},
    )
    r9 = [item for item in findings if item["rule"] == "R9"]

    assert r9[0]["severity"] == "hard"


def test_a_gps_gap_over_three_minutes_is_soft():
    points = trace_to_dump()
    del points[10:19]                  # a four and a half minute hole

    findings = rules.check_trip(
        trip(), slip(), points, {"status": "OK"}, DUMPSITE, {"MH 01 AA 1000": 16}, {}
    )
    gap = [item for item in findings if item["rule"] == "GPS_GAP"]

    assert gap[0]["severity"] == "soft"
    assert gap[0]["evidence"]["gapSeconds"] >= 180


def test_a_missing_slip_is_soft_not_a_pass_and_not_fraud():
    findings = rules.check_trip(
        trip(), None, trace_to_dump(), {"status": "OK"}, DUMPSITE,
        {"MH 01 AA 1000": 16}, {},
    )
    assert rule_ids(findings) == ["SLIP_MISSING"]
    assert rules.verdict_for(findings) == "REVIEW"


def test_a_missing_trace_is_soft_and_suppresses_r5():
    findings = rules.check_trip(
        trip(), slip(), [], None, DUMPSITE, {"MH 01 AA 1000": 16}, {}
    )
    assert "TRACE_MISSING" in rule_ids(findings)
    assert "R5" not in rule_ids(findings)


def test_a_slip_that_failed_ingestion_is_flagged():
    findings = rules.check_trip(
        trip(), slip(status="ERROR", error="AccessDenied"), trace_to_dump(),
        {"status": "OK"}, DUMPSITE, {"MH 01 AA 1000": 16}, {},
    )
    assert "EVIDENCE_ERROR" in rule_ids(findings)


# -------------------------------------------------------------------- R10
def test_r10_passes_for_a_plausible_claim():
    assert rules.check_drain_volume(DRAIN, 120) == []


def test_r10_is_soft_for_more_silt_than_the_drain_holds():
    findings = rules.check_drain_volume(DRAIN, 900)

    assert findings[0]["rule"] == "R10"
    assert findings[0]["severity"] == "soft"


def test_r10_computes_the_ceiling_when_it_was_not_stored():
    drain = {"lengthM": 200, "widthM": 2.0, "depthM": 1.0}
    assert rules.check_drain_volume(drain, 100) == []
    assert rules.check_drain_volume(drain, 2000)[0]["rule"] == "R10"


def test_r10_cannot_fire_without_dimensions():
    assert rules.check_drain_volume({}, 10_000) == []


# ---------------------------------------------------------------- rollups
@pytest.mark.parametrize(
    "severities,expected",
    [([], "VERIFIED"), (["soft"], "REVIEW"), (["hard"], "HOLD"), (["soft", "hard"], "HOLD")],
)
def test_trip_rollup(severities, expected):
    findings = [rules.finding("R1", severity, "x") for severity in severities]
    assert rules.verdict_for(findings) == expected


@pytest.mark.parametrize(
    "verdicts,colour",
    [
        (["VERIFIED", "VERIFIED"], "GREEN"),
        (["VERIFIED", "REVIEW"], "AMBER"),
        (["REVIEW", "HOLD"], "RED"),
        ([], "GREEN"),
    ],
)
def test_drain_rollup(verdicts, colour):
    assert rules.colour_for(verdicts) == colour


def test_approving_a_drain_releases_everything_it_withheld():
    assert rules.apply_decision("APPROVE", 60.0, 10.0, 0.0) == (70.0, 0.0, 0.0)


def test_holding_a_drain_pushes_review_into_held():
    assert rules.apply_decision("HOLD", 60.0, 10.0, 5.0) == (60.0, 0.0, 15.0)


def test_no_decision_leaves_the_evidence_to_speak():
    assert rules.apply_decision(None, 60.0, 10.0, 5.0) == (60.0, 10.0, 5.0)


def test_summarise_counts_colours_and_money():
    summary = rules.summarise(
        [
            {"claimedTonnes": 100, "verifiedTonnes": 100, "reviewTonnes": 0,
             "heldTonnes": 0, "verdict": "GREEN"},
            {"claimedTonnes": 50, "verifiedTonnes": 0, "reviewTonnes": 50,
             "heldTonnes": 0, "verdict": "AMBER"},
            {"claimedTonnes": 30, "verifiedTonnes": 0, "reviewTonnes": 0,
             "heldTonnes": 30, "verdict": "RED", "decision": "HOLD"},
        ],
        rate=1800,
    )

    assert summary["claimedTonnes"] == 180
    assert summary["heldRupees"] == 54000
    assert (summary["green"], summary["amber"], summary["red"]) == (1, 1, 1)
    assert summary["decided"] == 1


def test_evaluate_on_an_empty_bill_does_not_raise():
    result = rules.evaluate({"bill": {}, "drains": [], "trips": []})

    assert result["summary"]["claimedTonnes"] == 0
    assert result["drains"] == {}
