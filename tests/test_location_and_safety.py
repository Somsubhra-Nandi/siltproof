"""The Location route parser, and the safety rails on the scripts.

Nothing in this repo may reach AWS without an explicit --live, and --live must
say what it is about to spend first. These tests hold that line.
"""

import json

import pytest

import dataset as ds
import gen_trips
import osm_drains
import reset as reset_script
import seed as seed_script
from common import location


# ------------------------------------------------------------ route parsing
def test_route_geometry_and_summary_are_parsed(fixture_response):
    parsed = location.parse_route(fixture_response("location_calculate_routes"))

    assert parsed["ok"] is True
    assert len(parsed["points"]) == 21
    assert parsed["distanceM"] == 6420
    assert parsed["durationS"] == 1080
    assert all(len(point) == 2 for point in parsed["points"])


def test_the_parser_finds_geometry_wherever_it_sits():
    """Written defensively: the live shape could not be checked offline."""
    response = {
        "Routes": [
            {
                "Legs": [
                    {
                        "VehicleLegDetails": {
                            "Summary": {"Overview": {"Distance": 1200, "Duration": 300}}
                        },
                        "Geometry": {"LineString": [[72.87, 19.07], [72.88, 19.08]]},
                    }
                ]
            }
        ]
    }
    parsed = location.parse_route(response)

    assert parsed["points"] == [[72.87, 19.07], [72.88, 19.08]]
    assert parsed["distanceM"] == 1200
    assert parsed["durationS"] == 300


def test_consecutive_duplicate_points_are_collapsed():
    response = {
        "Routes": [{"Legs": [{"Geometry": {"LineString": [
            [72.87, 19.07], [72.87, 19.07], [72.88, 19.08]
        ]}}]}]
    }

    assert len(location.parse_route(response)["points"]) == 2


def test_an_empty_route_is_not_ok():
    parsed = location.parse_route({"Routes": []})

    assert parsed["ok"] is False
    assert parsed["points"] == []
    assert parsed["distanceM"] is None


def test_mock_mode_anchors_the_route_on_the_real_endpoints():
    origin, destination = [72.8777, 19.0760], [72.9318, 19.1272]
    parsed = location.parse_route(location.calculate_route(origin, destination))

    assert parsed["ok"] is True
    assert parsed["points"][0] == pytest.approx(origin, abs=1e-6)
    assert parsed["points"][-1] == pytest.approx(destination, abs=1e-6)


# -------------------------------------------------------------- safety rails
def test_seed_dry_run_writes_a_plan_and_calls_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ds, "OUT", tmp_path)
    assert osm_drains.main(["--synthetic", "--center", "19.0760,72.8777"]) == 0
    assert gen_trips.main([]) == 0

    # Any boto3 client at all would be a bug in dry-run mode.
    import boto3

    def explode(*args, **kwargs):
        raise AssertionError("dry run must not create an AWS client")

    monkeypatch.setattr(boto3, "client", explode)
    monkeypatch.setattr(boto3, "resource", explode)

    assert seed_script.main([]) == 0

    dump = ds.load_json(tmp_path / "seed_dump.json")
    assert dump["mode"] == "dry-run"
    assert dump["estimatedCalls"]["textract"] == 0   # no slips rendered in this test
    assert len(dump["items"]) > 100

    output = capsys.readouterr().out
    assert "Dry run" in output
    assert "Billable calls" in output


def test_seed_dry_run_lists_every_item_type(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "OUT", tmp_path)
    osm_drains.main(["--synthetic", "--center", "19.0760,72.8777"])
    gen_trips.main([])
    seed_script.main([])

    items = ds.load_json(tmp_path / "seed_dump.json")["items"]
    kinds = {item["sk"].split("#")[0] for item in items}
    partitions = {item["pk"].split("#")[0] for item in items}

    assert kinds >= {"META", "DRAIN", "TRIP", "GROUNDTRUTH"}
    assert partitions == {"BILL", "VEHICLE", "DUMPSITE"}


def test_seed_live_refuses_without_a_bucket(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "OUT", tmp_path)
    monkeypatch.delenv("EVIDENCE_BUCKET", raising=False)
    osm_drains.main(["--synthetic", "--center", "19.0760,72.8777"])
    gen_trips.main([])

    assert seed_script.main(["--live", "--yes"]) == 2


def test_reset_dry_run_changes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ds, "OUT", tmp_path)
    osm_drains.main(["--synthetic", "--center", "19.0760,72.8777"])
    gen_trips.main([])

    import boto3

    monkeypatch.setattr(
        boto3, "client", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no AWS"))
    )

    assert reset_script.main([]) == 0

    output = capsys.readouterr().out
    assert "Dry run. Nothing changed." in output
    assert "18 drain items" in output


def test_reset_live_refuses_without_a_bucket(monkeypatch):
    monkeypatch.delenv("EVIDENCE_BUCKET", raising=False)

    assert reset_script.main(["--live", "--yes"]) == 2


def test_gen_trips_live_needs_confirmation(tmp_path, monkeypatch):
    """--live without --yes must stop at the prompt rather than spend money."""
    monkeypatch.setattr(ds, "OUT", tmp_path)
    osm_drains.main(["--synthetic", "--center", "19.0760,72.8777"])

    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")

    assert gen_trips.main(["--live"]) == 1


def test_the_photo_map_example_parses():
    rows = seed_script.read_photo_map(ds.DATA / "photo_map.example.csv")

    assert len(rows) == 7
    assert {row["role"] for row in rows} == {"before", "after", "load"}
    # The same file mapped to two drains is the R3 hero case.
    reused = [row for row in rows if row["filename"] == "IMG_0004.jpg"]
    assert {row["drainId"] for row in reused} == {"9", "14"}


def test_a_broken_photo_map_row_is_skipped_not_fatal(tmp_path):
    path = tmp_path / "map.csv"
    path.write_text(
        "filename,drainId,role,note\n"
        "good.jpg,3,load,fine\n"
        "bad.jpg,,after,missing drain\n"
        "worse.jpg,4,sideways,bad role\n"
    )

    rows = seed_script.read_photo_map(path)

    assert len(rows) == 1
    assert rows[0]["filename"] == "good.jpg"


def test_dynamo_rejects_nothing_the_seed_writes(tmp_path, monkeypatch):
    """Floats must become Decimals, or every write fails at run time."""
    from common import store

    monkeypatch.setattr(ds, "OUT", tmp_path)
    osm_drains.main(["--synthetic", "--center", "19.0760,72.8777"])
    gen_trips.main([])
    seed_script.main([])

    items = ds.load_json(tmp_path / "seed_dump.json")["items"]
    converted = store.to_dynamo(items)

    assert "float" not in json.dumps(converted, default=lambda value: type(value).__name__)
