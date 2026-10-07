"""Photo evidence: EXIF GPS and timestamp, plus a perceptual hash.

A photo with no EXIF is not an error — WhatsApp strips EXIF, and the plan calls
that out (section 6). Such a photo is kept and flagged so the rules can treat
the missing GPS or time as a failure with a readable reason.
"""

import datetime
import io

import imagehash
from PIL import Image, ExifTags

GPS_IFD = 0x8825
EXIF_IFD = 0x8769

TAG_DATETIME_ORIGINAL = 36867   # in the Exif IFD
TAG_DATETIME_DIGITIZED = 36868  # in the Exif IFD
TAG_DATETIME = 306              # in IFD0, the fallback
TAG_OFFSET_ORIGINAL = 36881     # OffsetTimeOriginal, e.g. '+05:30'

GPS_TAGS = {name: number for number, name in ExifTags.GPSTAGS.items()}


def _rational(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(value.numerator) / float(value.denominator)
        except (AttributeError, ZeroDivisionError, TypeError, ValueError):
            return None


def _dms_to_degrees(dms, ref):
    if not dms or len(dms) < 3:
        return None

    parts = [_rational(part) for part in dms[:3]]
    if any(part is None for part in parts):
        return None

    degrees = parts[0] + parts[1] / 60.0 + parts[2] / 3600.0

    if isinstance(ref, bytes):
        ref = ref.decode("ascii", "ignore")
    if str(ref).strip().upper() in ("S", "W"):
        degrees = -degrees

    return round(degrees, 7)


def _parse_exif_datetime(text, offset=None):
    """'2026:10:08 09:42:11' -> ISO 8601, keeping the offset when EXIF has one."""
    if not text:
        return None

    cleaned = str(text).strip().replace("/", ":")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y:%m:%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            stamp = datetime.datetime.strptime(cleaned, fmt)
        except ValueError:
            continue

        if offset:
            try:
                sign = 1 if str(offset).strip()[0] != "-" else -1
                hours, _, minutes = str(offset).strip()[1:].partition(":")
                delta = datetime.timedelta(hours=int(hours), minutes=int(minutes or 0))
                stamp = stamp.replace(tzinfo=datetime.timezone(sign * delta))
            except (ValueError, IndexError):
                pass

        return stamp.isoformat()

    return None


def read_exif(image_bytes):
    """Return GPS, timestamp and a list of problems. Never raises."""
    result = {
        "lat": None,
        "lon": None,
        "altitudeM": None,
        "timestamp": None,
        "hasGps": False,
        "hasTimestamp": False,
        "problems": [],
        "camera": None,
    }

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            exif = image.getexif()
            result["width"], result["height"] = image.size
    except Exception as exc:  # Pillow raises a wide variety on bad input.
        result["problems"].append(f"unreadable_image:{type(exc).__name__}")
        return result

    if not exif:
        result["problems"].append("no_exif")
        return result

    make = exif.get(271)
    model = exif.get(272)
    if make or model:
        result["camera"] = " ".join(str(part).strip() for part in (make, model) if part)

    # ---- timestamp
    try:
        exif_ifd = exif.get_ifd(EXIF_IFD) or {}
    except Exception:
        exif_ifd = {}

    offset = exif_ifd.get(TAG_OFFSET_ORIGINAL)
    stamp = (
        _parse_exif_datetime(exif_ifd.get(TAG_DATETIME_ORIGINAL), offset)
        or _parse_exif_datetime(exif_ifd.get(TAG_DATETIME_DIGITIZED), offset)
        or _parse_exif_datetime(exif.get(TAG_DATETIME), offset)
    )

    if stamp:
        result["timestamp"] = stamp
        result["hasTimestamp"] = True
    else:
        result["problems"].append("no_timestamp")

    # ---- gps
    try:
        gps = exif.get_ifd(GPS_IFD) or {}
    except Exception:
        gps = {}

    if not gps:
        result["problems"].append("no_gps")
        return result

    lat = _dms_to_degrees(gps.get(GPS_TAGS["GPSLatitude"]), gps.get(GPS_TAGS["GPSLatitudeRef"]))
    lon = _dms_to_degrees(gps.get(GPS_TAGS["GPSLongitude"]), gps.get(GPS_TAGS["GPSLongitudeRef"]))

    if lat is None or lon is None:
        result["problems"].append("gps_unparseable")
        return result

    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        result["problems"].append("gps_out_of_range")
        return result

    result["lat"], result["lon"] = lat, lon
    result["hasGps"] = True

    altitude = _rational(gps.get(GPS_TAGS["GPSAltitude"]))
    if altitude is not None:
        result["altitudeM"] = round(altitude, 2)

    return result


def perceptual_hash(image_bytes):
    """64-bit pHash as a hex string, or None if the image cannot be read."""
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            return str(imagehash.phash(image))
    except Exception:
        return None


def hamming(hex_a, hex_b):
    """Hamming distance between two pHash hex strings, or None."""
    if not hex_a or not hex_b or len(hex_a) != len(hex_b):
        return None
    try:
        return bin(int(hex_a, 16) ^ int(hex_b, 16)).count("1")
    except ValueError:
        return None
