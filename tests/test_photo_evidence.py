"""EXIF reading and perceptual hashing, including photos stripped of EXIF."""

import datetime
import io

import piexif
import pytest
from PIL import Image, ImageEnhance

from common import photo

import gen_photos


def jpeg_with_exif(lat, lon, stamp, colour=(90, 110, 90)):
    image = Image.new("RGB", (240, 180), colour)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)

    out = io.BytesIO()
    piexif.insert(gen_photos.exif_bytes(lat, lon, stamp), buffer.getvalue(), out)
    return out.getvalue()


def jpeg_without_exif(colour=(90, 110, 90)):
    buffer = io.BytesIO()
    Image.new("RGB", (240, 180), colour).save(buffer, format="JPEG")
    return buffer.getvalue()


# -------------------------------------------------------------------- exif
def test_gps_and_timestamp_round_trip():
    stamp = datetime.datetime(
        2026, 9, 28, 9, 42, 11, tzinfo=datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    )
    exif = photo.read_exif(jpeg_with_exif(19.0760, 72.8777, stamp))

    assert exif["hasGps"] is True
    assert exif["lat"] == pytest.approx(19.0760, abs=1e-4)
    assert exif["lon"] == pytest.approx(72.8777, abs=1e-4)
    assert exif["hasTimestamp"] is True
    assert exif["timestamp"].startswith("2026-09-28T09:42:11")
    assert exif["timestamp"].endswith("+05:30")
    assert exif["problems"] == []


def test_southern_and_western_hemispheres():
    stamp = datetime.datetime(2026, 9, 28, 9, 42, 11)
    exif = photo.read_exif(jpeg_with_exif(-33.8688, -70.6693, stamp))

    assert exif["lat"] == pytest.approx(-33.8688, abs=1e-4)
    assert exif["lon"] == pytest.approx(-70.6693, abs=1e-4)


def test_a_photo_with_no_exif_is_flagged_not_fatal():
    """WhatsApp strips EXIF. The photo still ingests, with the problem recorded."""
    exif = photo.read_exif(jpeg_without_exif())

    assert exif["hasGps"] is False
    assert exif["hasTimestamp"] is False
    assert exif["lat"] is None
    assert "no_exif" in exif["problems"]


def test_garbage_bytes_do_not_raise():
    exif = photo.read_exif(b"this is not an image at all")

    assert exif["hasGps"] is False
    assert any(problem.startswith("unreadable_image") for problem in exif["problems"])


def test_zero_island_is_rejected():
    """0,0 is what a camera writes when it never got a fix."""
    stamp = datetime.datetime(2026, 9, 28, 9, 42, 11)
    exif = photo.read_exif(jpeg_with_exif(0.0, 0.0, stamp))

    assert exif["hasGps"] is False
    assert "gps_out_of_range" in exif["problems"]


# ------------------------------------------------------------------- phash
def test_identical_bytes_hash_identically():
    data = jpeg_without_exif()

    assert photo.hamming(photo.perceptual_hash(data), photo.perceptual_hash(data)) == 0


def test_phash_survives_re_encoding_and_a_brightness_change():
    original = Image.new("RGB", (400, 300), (80, 90, 100))
    for x in range(0, 400, 40):
        for y in range(0, 300, 30):
            original.paste((200 - x // 4, 60 + y // 3, 120), (x, y, x + 20, y + 15))

    buffer = io.BytesIO()
    original.save(buffer, format="JPEG", quality=92)
    first = photo.perceptual_hash(buffer.getvalue())

    brighter = io.BytesIO()
    ImageEnhance.Brightness(original).enhance(1.2).save(brighter, format="JPEG", quality=70)
    second = photo.perceptual_hash(brighter.getvalue())

    assert photo.hamming(first, second) <= 12


def test_different_images_hash_far_apart():
    """Two different drain scenes must be far outside the duplicate threshold.

    Drawn from gen_photos rather than flat colour blocks on purpose: pHash
    reads the low-frequency DCT, and a two-tone image has almost no signal
    there, so synthetic blocks land a few bits apart no matter how different
    they look. Real photographs, and these scenes, are textured.
    """
    import dataset as ds

    def encode(seed):
        scene = gen_photos.draw_scene("after", f"drain {seed}", ds.rng("test", seed))
        buffer = io.BytesIO()
        scene.save(buffer, format="JPEG", quality=88)
        return photo.perceptual_hash(buffer.getvalue())

    distances = [
        photo.hamming(encode(f"drain-{a}"), encode(f"drain-{b}"))
        for a, b in ((1, 2), (3, 7), (5, 11), (9, 14))
    ]

    assert min(distances) > 12, distances


def test_phash_of_unreadable_bytes_is_none():
    assert photo.perceptual_hash(b"nope") is None


@pytest.mark.parametrize(
    "a,b,expected",
    [("ffff", "ffff", 0), ("0000", "ffff", 16), ("abc", None, None), ("abcd", "ab", None)],
)
def test_hamming_edge_cases(a, b, expected):
    assert photo.hamming(a, b) == expected
