"""The data generators: geometry, the Overpass parser, and the planted cases."""

import datetime
import json

import pytest

import dataset as ds
import gen_trips
import osm_drains
from common import geo

CENTER = "19.0760,72.8777"


@pytest.fixture
def generated(tmp_path, monkeypatch):
    """A full synthetic dataset in a temp directory: drains, trips, traces."""
    monkeypatch.setattr(ds, "OUT", tmp_path)

    assert osm_drains.main(["--synthetic", "--center", CENTER]) == 0
    assert gen_trips.main([]) == 0

    return {
        "out": tmp_path,
        "drains": ds.load_json(tmp_path / "drains.geojson"),
        "dumpsite": ds.load_json(tmp_path / "dumpsite.geojson"),
        "trips": ds.load_json(tmp_path / "trips.json")["trips"],
        "truth": ds.load_json(tmp_path / "ground_truth.json"),
    }


# ----------------------------------------------------------------- geometry
def test_eighteen_buffered_drains(generated):
    features = generated["drains"]["features"]

    assert len(features) == 18
    assert sorted(int(f["properties"]["drainId"]) for f in features) == list(range(1, 19))

    for feature in features:
        assert feature["geometry"]["type"] == "Polygon"
        ring = feature["geometry"]["coordinates"][0]
        assert len(ring) > 4
        assert ring[0] == ring[-1], "ring must be closed"
        assert feature["properties"]["widthM"] > 0
        assert feature["properties"]["depthM"] > 0


def test_the_buffer_really_is_thirty_metres(generated):
    """A point on the centreline is inside; one 45 m to the side is not."""
    feature = generated["drains"]["features"][0]
    centreline = feature["properties"]["centreline"]
    geometry = feature["geometry"]

    midpoint = geo.interpolate(centreline[0], centreline[1], 0.5)
    assert geo.point_in_geometry(midpoint, geometry)

    import math

    bearing = math.atan2(
        centreline[1][1] - centreline[0][1], centreline[1][0] - centreline[0][0]
    ) + math.pi / 2
    far = geo.offset_m(midpoint, math.cos(bearing) * 45, math.sin(bearing) * 45)
    assert not geo.point_in_geometry(far, geometry)

    near = geo.offset_m(midpoint, math.cos(bearing) * 20, math.sin(bearing) * 20)
    assert geo.point_in_geometry(near, geometry)


def test_no_city_is_hard_coded():
    """Without a location, the generator refuses rather than inventing one."""
    with pytest.raises(SystemExit):
        osm_drains.main(["--synthetic"])


def test_overpass_response_is_parsed_into_sections():
    payload = {
        "elements": [
            {
                "type": "way",
                "id": 101,
                "tags": {"waterway": "drain", "name": "Nala 1"},
                "geometry": [
                    {"lat": 19.07 + i * 0.001, "lon": 72.87 + i * 0.001} for i in range(6)
                ],
            },
            {
                "type": "way",
                "id": 102,
                "tags": {"waterway": "ditch"},
                "geometry": [{"lat": 19.08, "lon": 72.88}],   # too short to use
            },
            {"type": "node", "id": 103},                      # not a way
        ]
    }

    lines = osm_drains.lines_from_overpass(payload)

    assert len(lines) == 1
    assert lines[0]["osmId"] == 101
    assert lines[0]["name"] == "Nala 1"
    assert lines[0]["lengthM"] > 0


def test_long_ways_are_split_into_sections():
    line = [{"lat": 19.07 + i * 0.002, "lon": 72.87} for i in range(40)]
    payload = {"elements": [{"type": "way", "id": 1, "tags": {"waterway": "canal"},
                             "geometry": line}]}

    lines = osm_drains.lines_from_overpass(payload)
    sections = osm_drains.split_long_lines(lines, wanted=18)

    assert len(sections) > 1
    assert all(section["lengthM"] <= 900 for section in sections)


# ------------------------------------------------------------------- totals
def test_the_plan_headline_numbers(generated):
    totals = generated["truth"]["totals"]

    assert totals["claimedTonnes"] == ds.TARGETS["claimedTonnes"]        # 1240
    assert totals["heldTonnes"] == ds.TARGETS["heldTonnes"]              # 370
    assert totals["heldRupees"] == ds.TARGETS["heldRupees"]              # 6.66 lakh
    assert totals["reviewTonnes"] == ds.TARGETS["reviewTonnes"]          # 65
    assert totals["verifiedTonnes"] == ds.TARGETS["verifiedTonnes"]      # 805
    assert totals["verifiedAfterApprovals"] == ds.TARGETS["verifiedAfterApprovals"]  # 870


def test_about_one_hundred_and_twenty_trips(generated):
    assert 100 <= len(generated["trips"]) <= 140


def test_each_drain_trips_sum_to_its_claim(generated):
    by_drain = {}
    for trip in generated["trips"]:
        by_drain.setdefault(trip["drainId"], 0)
        by_drain[trip["drainId"]] += trip["claimedTonnes"]

    for drain_id, claimed in ds.DRAIN_CLAIMED.items():
        assert round(by_drain[drain_id], 1) == claimed, drain_id


def test_every_trip_has_a_slip_and_a_trace(generated):
    for trip in generated["trips"]:
        assert trip["slipKey"].startswith("slips/B1/")
        assert trip["traceKey"].startswith("traces/B1/")
        assert (generated["out"] / "evidence" / trip["traceKey"]).exists()


# ------------------------------------------------------------ planted cases
def test_drain_colours_match_the_plan(generated):
    drains = generated["truth"]["drains"]

    assert drains["14"]["expectedColour"] == "RED"
    assert drains["3"]["expectedColour"] == "RED"
    assert drains["11"]["expectedColour"] == "RED"
    assert drains["16"]["expectedColour"] == "RED"
    assert drains["6"]["expectedColour"] == "AMBER"
    assert drains["8"]["expectedColour"] == "AMBER"

    clean = set(ds.DRAIN_CLAIMED) - set(ds.PLANTED)
    assert all(drains[drain_id]["expectedColour"] == "GREEN" for drain_id in clean)


def test_drain_14_never_reaches_the_dump_site(generated):
    dumpsite = generated["dumpsite"]["features"][0]

    for trip in [t for t in generated["trips"] if t["drainId"] == "14"]:
        trace = ds.load_json(generated["out"] / "evidence" / trip["traceKey"])
        inside = any(
            geo.point_in_geometry([point["lon"], point["lat"]], dumpsite["geometry"])
            for point in trace["points"]
        )
        assert not inside, f"{trip['tripId']} should never enter the dump site"
        assert trip["expectedVerdict"] == "HOLD"
        assert "R5" in trip["expectedHardFails"]


def test_drain_14_slip_is_printed_before_the_truck_arrives(generated):
    trip = next(t for t in generated["trips"] if t["drainId"] == "14")

    arrival = datetime.datetime.fromisoformat(trip["arrivalTime"])
    slip_in = datetime.datetime.strptime(trip["slip"]["timeIn"], "%H:%M").time()
    minutes_early = (arrival.hour * 60 + arrival.minute) - (slip_in.hour * 60 + slip_in.minute)

    assert minutes_early == gen_trips.R8_SLIP_EARLY_MIN
    assert "R8" in trip["expectedHardFails"]


def test_drain_11_overloads_a_ten_tonne_truck(generated):
    flagged = [t for t in generated["trips"]
               if t["drainId"] == "11" and t["expectedVerdict"] == "HOLD"]

    assert len(flagged) == 2
    for trip in flagged:
        assert trip["slip"]["net"] > trip["capacityTonnes"]
        assert trip["capacityTonnes"] == gen_trips.OVERLOAD_CAPACITY
        assert trip["expectedHardFails"] == ["R7"]


def test_drain_16_is_in_two_places_at_once(generated):
    pair = [t for t in generated["trips"]
            if t["drainId"] == "16" and t["marker"] == "R6_IMPOSSIBLE"]
    assert len(pair) == 2

    first, second = sorted(pair, key=lambda trip: trip["startTime"])
    assert first["vehicleNo"] == second["vehicleNo"]

    last = ds.load_json(generated["out"] / "evidence" / first["traceKey"])["points"][-1]
    nxt = ds.load_json(generated["out"] / "evidence" / second["traceKey"])["points"][0]

    minutes = (
        datetime.datetime.fromisoformat(nxt["t"]) - datetime.datetime.fromisoformat(last["t"])
    ).total_seconds() / 60
    km = geo.haversine_m([last["lon"], last["lat"]], [nxt["lon"], nxt["lat"]]) / 1000

    assert minutes == pytest.approx(gen_trips.R6_GAP_MINUTES, abs=0.5)
    assert km == pytest.approx(gen_trips.R6_DISTANCE_KM, abs=0.5)
    assert km / (minutes / 60) > 40, "the implied speed must break the 40 km/h rule"


def test_drain_6_has_one_honest_gap_and_nothing_else(generated):
    trips = [t for t in generated["trips"] if t["drainId"] == "6"]
    flagged = [t for t in trips if t["expectedVerdict"] == "REVIEW"]

    assert len(flagged) == 1
    assert flagged[0]["expectedSoftFails"] == ["GPS_GAP"]
    assert flagged[0]["expectedHardFails"] == []
    assert all(t["expectedVerdict"] == "VERIFIED" for t in trips if t not in flagged)

    points = ds.load_json(generated["out"] / "evidence" / flagged[0]["traceKey"])["points"]
    stamps = [datetime.datetime.fromisoformat(point["t"]) for point in points]
    gaps = [(stamps[i + 1] - stamps[i]).total_seconds() for i in range(len(stamps) - 1)]

    assert max(gaps) > 180, "the gap has to exceed the three-minute soft threshold"


def test_traces_are_thirty_second_samples(generated):
    trip = next(t for t in generated["trips"] if t["drainId"] == "1")
    points = ds.load_json(generated["out"] / "evidence" / trip["traceKey"])["points"]
    stamps = [datetime.datetime.fromisoformat(point["t"]) for point in points]
    gaps = {(stamps[i + 1] - stamps[i]).total_seconds() for i in range(len(stamps) - 1)}

    assert gaps == {float(ds.TRACE_INTERVAL_S)}


def test_r10_is_satisfied_everywhere(generated):
    """No drain should fail the plausibility rule; it is not a planted case."""
    for feature in generated["drains"]["features"]:
        properties = feature["properties"]
        assert properties["plausibleMaxTonnes"] > properties["claimedTonnes"]


def test_the_fleet_covers_every_trip(generated):
    capacities = {v["vehicleNo"]: v["capacityTonnes"] for v in ds.vehicles()}

    for trip in generated["trips"]:
        assert trip["vehicleNo"] in capacities
        if trip["marker"] != "R7_OVERLOAD":
            assert trip["claimedTonnes"] <= capacities[trip["vehicleNo"]]


def test_generation_is_reproducible(tmp_path, monkeypatch):
    def run(target):
        monkeypatch.setattr(ds, "OUT", target)
        osm_drains.main(["--synthetic", "--center", CENTER])
        gen_trips.main([])
        return json.dumps(ds.load_json(target / "ground_truth.json"), sort_keys=True)

    first = run(tmp_path / "a")
    second = run(tmp_path / "b")

    assert first == second
