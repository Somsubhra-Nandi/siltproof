"""The committed offline snapshot (frontend/public/data/demo).

It is generated, but it is also what the demo falls back to on camera, so the
numbers, the hero drain's evidence and its honesty are checked here.
"""

import json
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
DEMO = REPO / "frontend" / "public" / "data" / "demo"
pytestmark = pytest.mark.skipif(not (DEMO / "bill.json").exists(), reason="no snapshot")


def load(name):
    return json.loads((DEMO / f"{name}.json").read_text())


def test_the_headline_numbers():
    summary = load("bill")["summary"]
    assert (summary["claimedTonnes"], summary["verifiedTonnes"],
            summary["reviewTonnes"], summary["heldTonnes"]) == (1240, 805, 65, 370)
    assert summary["heldRupees"] == 666000
    assert summary["claimedRupees"] == 2232000


def test_no_presigned_link_is_committed():
    for path in DEMO.glob("*.json"):
        assert "X-Amz-" not in path.read_text(), path.name


def test_image_links_are_local_files_or_null():
    for path in DEMO.glob("drain-*.json"):
        drain = json.loads(path.read_text())
        links = [photo["imageUrl"] for photo in drain["photos"]]
        links += [trip["slipImageUrl"] for trip in drain["trips"]]
        for link in filter(None, links):
            assert link.startswith("/data/demo/evidence/"), link
            assert (DEMO.parent.parent / link.lstrip("/")).exists(), link


def test_every_drain_has_its_images_offline():
    slips = photos = 0
    for drain_id in range(1, 19):
        drain = load(f"drain-{drain_id}")
        for trip in drain["trips"]:
            assert trip["slipImageUrl"] == f"/data/demo/evidence/{trip['slipKey']}", trip["tripId"]
            slips += 1
        for photo in drain["photos"]:
            assert photo["imageUrl"] == f"/data/demo/evidence/{photo['s3Key']}", photo["s3Key"]
            photos += 1
    assert (slips, photos) == (117, 40)


def test_r8_and_the_summary_agree_on_the_minutes():
    drain = load("drain-14")
    r8 = [f["message"] for t in drain["trips"] for f in t["findings"] if f["rule"] == "R8"]
    assert len(r8) == 18
    assert "44 minutes after the slip's time-in" in r8[0]
    assert "40 minutes" not in json.dumps(drain)


def test_no_canned_answer_claims_a_model():
    for path in DEMO.glob("drain-*.json"):
        drain = json.loads(path.read_text())
        assert drain["summaryModelId"] is None
        if drain["summary"]:
            assert drain["summary"].startswith("Offline summary, no model was called."), path.name
        for photo in drain["photos"]:
            assert photo["bedrock"]["modelId"] is None and photo["bedrock"]["mocked"], path.name
