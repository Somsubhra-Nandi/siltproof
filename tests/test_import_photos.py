"""data/import_photos.py: licensed photographs in place of the generated ones."""

import csv
import io
import json
import random

import piexif
import pytest
from PIL import Image, ImageDraw

import import_photos as ip
from common.rules import PHASH_DUPLICATE_MAX

SLOTS = {
    "photos/B1/drain1/before-01.jpg": {"drainId": "1", "role": "before", "notes": "silted"},
    "photos/B1/drain9/after-01.jpg": {"drainId": "9", "role": "after", "notes": "cleared"},
    "photos/B1/drain3/load-01.jpg": {"drainId": "3", "role": "load", "loadType": "debris", "notes": "debris"},
    "photos/B1/drain14/after-02.jpg": {"drainId": "14", "role": "after", "notes": "cleared",
                                       "reusedFrom": "photos/B1/drain9/after-01.jpg", "reuseKind": "exact copy"},
    "photos/B1/drain14/after-03.jpg": {"drainId": "14", "role": "after", "notes": "cleared",
                                       "reusedFrom": "photos/B1/drain9/after-01.jpg",
                                       "reuseKind": "cropped and brightened copy"},
}


def gps_exif(lat, lon, make=b"SiltProof"):
    def dms(value):
        d = int(value)
        m = int((value - d) * 60)
        return ((d, 1), (m, 1), (int(((value - d) * 60 - m) * 6000), 100))

    return piexif.dump({
        "0th": {piexif.ImageIFD.Make: make},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: b"2026:09:24 10:00:00"},
        "GPS": {piexif.GPSIFD.GPSLatitudeRef: b"N", piexif.GPSIFD.GPSLatitude: dms(lat),
                piexif.GPSIFD.GPSLongitudeRef: b"E", piexif.GPSIFD.GPSLongitude: dms(lon)},
        "1st": {}, "thumbnail": None,
    })


def scene(kind, size=(1800, 1350)):
    """Photographs that are clearly different to a perceptual hash: seeded block textures."""
    rng = random.Random(kind)
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    cols, rows = 12, 9
    bw, bh = size[0] // cols, size[1] // rows
    for x in range(cols):
        for y in range(rows):
            grey = rng.randint(0, 255)
            draw.rectangle([x * bw, y * bh, (x + 1) * bw, (y + 1) * bh], fill=(grey, grey, grey))
    return image


def save(image, path, exif=None):
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    data = buffer.getvalue()
    if exif:
        out = io.BytesIO()
        piexif.insert(exif, data, out)
        data = out.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@pytest.fixture
def work(tmp_path):
    """A generator work folder: the manifest and the generated photos with their simulated tags."""
    root = tmp_path / "work"
    for index, key in enumerate(SLOTS):
        save(Image.new("RGB", (900, 675), "grey"), root / "evidence" / key, gps_exif(19.07 + index / 1000, 72.87))
    (root / "mock_manifest.json").write_text(json.dumps({"slips": {}, "photos": SLOTS}))
    return root


ROWS = [
    ("d1.jpg", "1", "before", 0),
    ("d9.jpg", "9", "after", 1),
    ("d3.jpg", "3", "load", 2),
]


@pytest.fixture
def supplied(tmp_path):
    folder = tmp_path / "supplied"
    for name, _, _, kind in ROWS:
        # A stock photo's own camera and location, which must never be copied.
        save(scene(kind), folder / name, gps_exif(51.5, 0.12, make=b"StockCamera"))
    sources = tmp_path / "sources.csv"
    with open(sources, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(ip.COLUMNS + ip.OPTIONAL)
        for name, drain, role, _ in ROWS:
            writer.writerow([name, drain, role, f"https://example.org/{name}", "A. Photographer", "CC-BY-4.0",
                             "https://creativecommons.org/licenses/by/4.0/", f"observed {name}", ""])
    return sources, folder


def test_refuses_to_write_without_agreeing_to_simulated_tags(work, supplied):
    sources, folder = supplied
    before = (work / "evidence/photos/B1/drain9/after-01.jpg").read_bytes()
    with pytest.raises(ip.PhotoImportError, match="accept-simulated-tags"):
        ip.apply(work, sources, folder)
    assert (work / "evidence/photos/B1/drain9/after-01.jpg").read_bytes() == before


def test_replaces_pixels_keeps_simulated_tags_and_rebuilds_the_reuse(work, supplied):
    sources, folder = supplied
    generated = piexif.load(str(work / "evidence/photos/B1/drain9/after-01.jpg"))
    credits = ip.apply(work, sources, folder, accept_simulated_tags=True)

    assert len(credits) == 5
    assert {c["key"]: c["derivedFrom"] for c in credits}["photos/B1/drain14/after-02.jpg"] == \
        "photos/B1/drain9/after-01.jpg"

    path = work / "evidence/photos/B1/drain9/after-01.jpg"
    with Image.open(path) as image:
        assert image.size == ip.SIZE
    tags = piexif.load(str(path))
    # The slot's simulated location, not the stock photo's own.
    assert tags["GPS"][piexif.GPSIFD.GPSLatitude] == generated["GPS"][piexif.GPSIFD.GPSLatitude]
    assert tags["0th"][piexif.ImageIFD.Make] == b"SiltProof"
    assert b"simulated" in tags["0th"][piexif.ImageIFD.ImageDescription]
    assert b"StockCamera" not in path.read_bytes()

    hashes = {key: ip.phash_of((work / "evidence" / key).read_bytes()) for key in SLOTS}
    source = hashes["photos/B1/drain9/after-01.jpg"]
    assert hashes["photos/B1/drain14/after-02.jpg"] - source == 0
    assert hashes["photos/B1/drain14/after-03.jpg"] - source <= PHASH_DUPLICATE_MAX
    assert hashes["photos/B1/drain1/before-01.jpg"] - source > PHASH_DUPLICATE_MAX

    manifest = json.loads((work / "mock_manifest.json").read_text())["photos"]
    assert manifest["photos/B1/drain9/after-01.jpg"]["notes"] == "observed d9.jpg"
    assert manifest["photos/B1/drain14/after-02.jpg"]["notes"] == "observed d9.jpg"
    assert manifest["photos/B1/drain3/load-01.jpg"]["loadType"] == "debris"


def test_rejects_disallowed_licences_gaps_and_repeats():
    fill, _ = ip.slots({"photos": SLOTS})
    rows = [
        {"file": "a.jpg", "drainId": "1", "role": "before", "sourceUrl": "https://x", "author": "A",
         "licence": "CC-BY-ND-4.0"},
        {"file": "a.jpg", "drainId": "9", "role": "after", "sourceUrl": "https://x", "author": "A",
         "licence": "CC0-1.0"},
    ]
    problems = "\n".join(ip.validate_rows(rows, fill))
    assert "CC-BY-ND-4.0" in problems
    assert "no photograph for drain 3 load" in problems
    assert "a.jpg is used for more than one slot" in problems


def test_an_unrelated_near_duplicate_is_refused(work, supplied, tmp_path):
    sources, folder = supplied
    # The drain 1 photo is the drain 9 photo again: R3 would call it a copy.
    (folder / "d1.jpg").write_bytes((folder / "d9.jpg").read_bytes())
    before = (work / "evidence/photos/B1/drain1/before-01.jpg").read_bytes()
    with pytest.raises(ip.PhotoImportError, match="bits apart"):
        ip.apply(work, sources, folder, accept_simulated_tags=True)
    assert (work / "evidence/photos/B1/drain1/before-01.jpg").read_bytes() == before


def test_small_or_portrait_photographs_are_refused(tmp_path):
    folder = tmp_path / "p"
    save(Image.new("RGB", (800, 600)), folder / "small.jpg")
    save(Image.new("RGB", (1200, 1800)), folder / "tall.jpg")
    problems = "\n".join(ip.validate_images([{"file": "small.jpg"}, {"file": "tall.jpg"}], folder))
    assert "small.jpg: 800x600" in problems
    assert "tall.jpg" in problems and "portrait" in problems
