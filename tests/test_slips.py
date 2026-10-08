"""Weighbridge slip images must say what their trip record says.

A stale image once showed another truck's plate and a different time-in next
to the right extracted fields. These tests check the printed values through
the generator's own record of them (a PNG text chunk written from the same
dict the drawing uses), and that every checked field really reaches the pixels.
"""

import copy

import pytest

import dataset as ds
import gen_slips
import gen_trips
import osm_drains

CENTER = "19.0760,72.8777"


@pytest.fixture
def trips(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "OUT", tmp_path)
    assert osm_drains.main(["--synthetic", "--center", CENTER]) == 0
    assert gen_trips.main([]) == 0
    return ds.load_json(tmp_path / "trips.json")["trips"]


def test_printed_values_come_from_the_trip(trips):
    for trip in trips:
        printed = gen_slips.printed_fields(trip)
        slip = trip["slip"]
        assert printed["ticketNo"] == slip["ticketNo"]
        assert printed["vehicleNo"] == slip["vehicleNo"] == trip["vehicleNo"]
        assert printed["gross"] == f"{slip['gross']:.2f} T"
        assert printed["tare"] == f"{slip['tare']:.2f} T"
        assert printed["net"] == f"{slip['net']:.2f} T"
        assert printed["timeIn"] == slip["timeIn"]
        assert printed["timeOut"] == slip["timeOut"]
        assert printed["tripId"] == trip["tripId"]


def test_rendered_slips_carry_their_printed_values(trips, tmp_path):
    assert gen_slips.main(["--limit", "60"]) == 0
    rendered = [t for t in trips if (tmp_path / "evidence" / t["slipKey"]).exists()]

    assert any(t["drainId"] == "14" for t in rendered)
    assert gen_slips.stale_slips(rendered, tmp_path / "evidence") == []

    for trip in rendered:
        printed = gen_slips.read_printed(tmp_path / "evidence" / trip["slipKey"])
        assert printed == gen_slips.printed_fields(trip)


def test_the_hero_slip_matches_its_trip(trips, tmp_path):
    """Drain 14's first slip, the one shown on camera."""
    assert gen_slips.main(["--limit", "60"]) == 0
    trip = next(t for t in trips if t["tripId"] == "14#001")
    printed = gen_slips.read_printed(tmp_path / "evidence" / trip["slipKey"])

    for field in gen_slips.CHECKED_FIELDS:
        assert printed[field] == gen_slips.expected_fields(trip)[field]


def test_a_slip_from_an_older_trip_is_reported_stale(trips, tmp_path):
    assert gen_slips.main(["--limit", "60"]) == 0
    trip = next(t for t in trips if t["tripId"] == "14#001")

    # The trip record moves on (a regenerated dataset) but the image does not.
    changed = copy.deepcopy(trip)
    changed["slip"]["vehicleNo"] = "MH 04 NP 6789"
    changed["slip"]["timeIn"] = "06:43"

    problems = dict(gen_slips.stale_slips([changed], tmp_path / "evidence"))
    assert "vehicleNo" in problems[trip["slipKey"]]
    assert "timeIn" in problems[trip["slipKey"]]


def test_a_slip_without_a_printed_record_is_reported(trips, tmp_path):
    trip = trips[0]
    path = tmp_path / "evidence" / trip["slipKey"]
    path.parent.mkdir(parents=True, exist_ok=True)
    gen_slips.draw_slip(trip).save(path, format="PNG")   # no pnginfo: an old image

    problems = dict(gen_slips.stale_slips([trip], tmp_path / "evidence"))
    assert "old generator" in problems[trip["slipKey"]]


def test_a_missing_slip_is_reported(trips, tmp_path):
    problems = dict(gen_slips.stale_slips(trips[:1], tmp_path / "nowhere"))
    assert problems[trips[0]["slipKey"]] == "missing"


@pytest.mark.parametrize(
    "field, value",
    [
        ("ticketNo", "WB-2026-00000"),
        ("vehicleNo", "MH 99 ZZ 9999"),
        ("gross", 31.5),
        ("tare", 3.3),
        ("net", 1.1),
        ("timeIn", "11:11"),
        ("timeOut", "12:12"),
    ],
)
def test_every_checked_field_is_drawn(trips, field, value):
    """Changing a field changes the pixels, so the record is not decoration."""
    trip = next(t for t in trips if t["tripId"] == "14#001")
    changed = copy.deepcopy(trip)
    changed["slip"][field] = value

    assert gen_slips.draw_slip(trip).tobytes() != gen_slips.draw_slip(changed).tobytes()


def test_check_mode_fails_on_stale_slips(trips, tmp_path, capsys):
    assert gen_slips.main(["--limit", "60"]) == 0
    assert gen_slips.main(["--check"]) == 1        # the rest were never rendered
    assert "stale" in capsys.readouterr().out

    assert gen_slips.main([]) == 0
    assert gen_slips.main(["--check"]) == 0


def test_rendering_is_deterministic(trips, tmp_path):
    trip = next(t for t in trips if t["tripId"] == "14#001")
    first = gen_slips.draw_slip(trip).tobytes()
    assert gen_slips.draw_slip(trip).tobytes() == first
