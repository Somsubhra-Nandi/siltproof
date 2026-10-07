"""Shared constants and helpers for the data generators.

The numbers in here are the dataset design. They are chosen so the demo totals
land exactly where plan section 6 asks:

    claimed 1,240 t · hold 370 t = Rs 6.66 lakh · review 65 t · verified 805 t

and approving the two review drains on camera takes verified to 870 t, which is
the plan's headline figure. See DECISIONS.md for why it is split that way.
"""

import json
import os
import pathlib
import random
import sys

# The generators reuse the Lambda's geodesy and key conventions.
REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

from common import geo  # noqa: E402,F401  (backend/common, re-exported)

# One definition of the DynamoDB key layout, shared with the Lambdas.
from common.store import (  # noqa: E402,F401
    bill_pk, drain_sk, trip_sk, evidence_pk, vehicle_pk, dumpsite_pk,
)

DATA = REPO / "data"
OUT = DATA / "out"
SIMULATED = DATA / "simulated"

BILL_ID = "B1"
RATE_PER_TONNE = 1800
SILT_DENSITY = 1.4          # tonnes per cubic metre, loose silt
DRAIN_BUFFER_M = 30.0       # geofence buffer around a drain centreline

# Rule R3: two photos are the same photo at or below this pHash Hamming
# distance. Measured on the generated set: exact copy 0, cropped and
# brightened copy ~7, nearest unrelated pair 16.
PHASH_DUPLICATE_MAX = 12
TRACE_INTERVAL_S = 30       # one GPS point every 30 s (plan section 6)
DRAIN_COUNT = 18

# Work window for the bill; rule R2 checks photo timestamps against it.
WORK_WINDOW = ("2026-09-21T06:00:00+05:30", "2026-10-04T19:00:00+05:30")

# Claimed tonnes per drain. Sums to 1,240.
DRAIN_CLAIMED = {
    "1": 48, "2": 56, "3": 126, "4": 60, "5": 44, "6": 70,
    "7": 52, "8": 55, "9": 58, "10": 46, "11": 85, "12": 62,
    "13": 50, "14": 192, "15": 54, "16": 75, "17": 42, "18": 65,
}

# Expected outcome per drain, from plan section 6's planted-case table.
#   hold_all / review_all   a photo-level rule taints every trip of the drain
#   trips                   only the named trips fail
PLANTED = {
    "14": {
        "colour": "RED",
        "scope": "hold_all",
        "story": "Truck detours to a vacant plot and never reaches the dump; "
                 "slip printed 40 min before GPS arrival; after-photo reused from drain 9",
        "photoRules": ["R3"],
        "tripRules": ["R5", "R8"],
    },
    "3": {
        "colour": "RED",
        "scope": "hold_all",
        "story": "Load is construction rubble passed off as silt",
        "photoRules": ["R4"],
        "tripRules": [],
    },
    "11": {
        "colour": "RED",
        "scope": "trips",
        "story": "Two slips show 14 t net on a 10 t truck",
        "photoRules": [],
        "tripRules": ["R7"],
    },
    "16": {
        "colour": "RED",
        "scope": "trips",
        "story": "Same truck logs two trips 7 min apart and 22 km apart",
        "photoRules": [],
        "tripRules": ["R6"],
    },
    "6": {
        "colour": "AMBER",
        "scope": "trips",
        "story": "4-minute GPS gap in an underpass; everything else is fine",
        "photoRules": [],
        "tripRules": ["GPS_GAP"],
    },
    "8": {
        "colour": "AMBER",
        "scope": "review_all",
        "story": "One photo taken outside the drain geofence",
        "photoRules": ["R1"],
        "tripRules": [],
    },
}

# Totals the generated dataset must hit. gen_trips.py asserts these.
TARGETS = {
    "claimedTonnes": 1240,
    "heldTonnes": 370,
    "reviewTonnes": 65,
    "verifiedTonnes": 805,
    "verifiedAfterApprovals": 870,
    "heldRupees": 666000,
}

# Fleet. The plate series is a CLI option so no city is baked in; capacity is a
# stand-in for the Vahan registry (plan section 4).
VEHICLE_CAPACITIES = [16, 14, 16, 10, 14, 16, 10, 14, 16, 14]

VEHICLE_SUFFIXES = [
    ("01", "AB", "1234"), ("01", "CD", "5678"), ("02", "EF", "9012"),
    ("02", "GH", "3456"), ("03", "JK", "7890"), ("03", "LM", "2345"),
    ("04", "NP", "6789"), ("04", "QR", "1357"), ("05", "ST", "2468"),
    ("05", "UV", "8024"),
]


def vehicles(series="MH"):
    """[{vehicleNo, capacityTonnes}, ...] for the fleet."""
    fleet = []
    for (district, letters, digits), capacity in zip(VEHICLE_SUFFIXES, VEHICLE_CAPACITIES):
        fleet.append(
            {
                "vehicleNo": f"{series} {district} {letters} {digits}",
                "capacityTonnes": capacity,
            }
        )
    return fleet


# ------------------------------------------------------------- s3 key layout
# These must stay in step with KEY_PATTERNS in backend/ingest/app.py.
def photo_key(drain_id, role, seq, bill_id=BILL_ID, ext="jpg"):
    return f"photos/{bill_id}/drain{drain_id}/{role}-{seq:02d}.{ext}"


def slip_key(drain_id, trip_no, bill_id=BILL_ID, ext="png"):
    return f"slips/{bill_id}/{drain_id}-{trip_no}.{ext}"


def trace_key(drain_id, trip_no, bill_id=BILL_ID):
    return f"traces/{bill_id}/{drain_id}-{trip_no}.json"


def trip_id(drain_id, trip_no):
    return f"{drain_id}#{trip_no}"


# ----------------------------------------------------------------- utilities
def rng(seed, salt=""):
    """A seeded generator, so every run of every generator is reproducible."""
    return random.Random(f"siltproof:{seed}:{salt}")


def ensure_out():
    OUT.mkdir(parents=True, exist_ok=True)
    return OUT


def save_json(path, payload):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")
    return path


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def rupees(tonnes):
    return int(round(tonnes * RATE_PER_TONNE))


def lakh(amount):
    return f"Rs {amount / 100000:.2f} lakh"


def parse_lat_lon(text, what="point"):
    """'19.0760,72.8777' -> [lon, lat]."""
    parts = str(text).replace(" ", "").split(",")
    if len(parts) != 2:
        raise ValueError(f"{what} must be 'lat,lon', got {text!r}")
    lat, lon = float(parts[0]), float(parts[1])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"{what} out of range: {text!r}")
    return [lon, lat]


def relative(path):
    """Shorter paths in log output."""
    try:
        return str(pathlib.Path(path).resolve().relative_to(REPO))
    except ValueError:
        return str(path)


def banner(title):
    print(f"\n{title}\n{'-' * len(title)}")


def env_flag(name):
    return os.environ.get(name, "") not in ("", "0", "false", "False")
