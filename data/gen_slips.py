#!/usr/bin/env python3
"""Render a weighbridge slip image for every trip.

    python data/gen_slips.py                  # all trips
    python data/gen_slips.py --limit 40       # the planted cases plus a sample

Entirely offline. The weighbridge is fictional and every slip carries a small
"SAMPLE DATA" mark, as plan section 6 requires.

A quarter of the slips are deliberately degraded - rotated, blurred, shadowed
and slightly warped - so the demo can show Textract coping with a crumpled
slip and reporting lower confidence. The hero case (drain 14) is always left
crisp, per the plan's risk note.

Reads data/out/trips.json, writes:
    data/out/evidence/slips/B1/<drain>-<trip>.png
    data/out/mock_manifest.json   (marks which slips are degraded)
"""

import argparse
import pathlib
import random as random_module
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import dataset as ds  # noqa: E402

from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402

WIDTH, HEIGHT = 760, 1000
MARGIN = 48

# Fictional, so nothing resembles a real weighbridge's paperwork.
WEIGHBRIDGE_NAME = "SHREE SAMPLE DHARAMKANTA"
WEIGHBRIDGE_SUB = "Public Weighbridge  |  Cap. 60 MT  |  Lic. SMPL/0000"

# Degrade this share of slips (never the hero drain).
DEGRADE_SHARE = 0.25
CRISP_DRAINS = {"14"}


def font(size, bold=False):
    """Pillow's built-in font scales, so no font files have to be shipped."""
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def draw_slip(trip):
    slip = trip["slip"]
    image = Image.new("RGB", (WIDTH, HEIGHT), (253, 253, 250))
    draw = ImageDraw.Draw(image)

    big = font(34)
    small = font(19)
    label = font(21)
    value = font(25)
    mono = font(27)

    y = MARGIN

    # ---- header
    draw.rectangle([MARGIN, y, WIDTH - MARGIN, y + 92], outline=(20, 20, 20), width=3)
    draw.text((WIDTH / 2, y + 26), WEIGHBRIDGE_NAME, font=big, fill=(10, 10, 10), anchor="mm")
    draw.text((WIDTH / 2, y + 62), WEIGHBRIDGE_SUB, font=small, fill=(60, 60, 60), anchor="mm")
    y += 92

    draw.text((WIDTH / 2, y + 26), "WEIGHMENT SLIP", font=label, fill=(10, 10, 10), anchor="mm")
    y += 54

    # ---- the small SAMPLE DATA mark the plan asks for
    draw.rectangle([WIDTH - MARGIN - 168, y, WIDTH - MARGIN, y + 30], outline=(150, 40, 40), width=2)
    draw.text(
        (WIDTH - MARGIN - 84, y + 15), "SAMPLE DATA", font=small, fill=(150, 40, 40), anchor="mm"
    )
    y += 48

    def row(name, text, *, gap=46, value_font=value):
        nonlocal y
        draw.text((MARGIN, y), name, font=label, fill=(70, 70, 70))
        draw.text((MARGIN + 250, y - 3), str(text), font=value_font, fill=(10, 10, 10))
        y += gap

    row("TICKET NO", slip["ticketNo"])
    row("DATE", slip["date"])
    row("VEHICLE NO", slip["vehicleNo"], value_font=mono)
    row("MATERIAL", "DRAIN SILT")
    row("SITE", slip["site"], value_font=small)

    y += 10
    draw.line([MARGIN, y, WIDTH - MARGIN, y], fill=(120, 120, 120), width=2)
    y += 28

    row("GROSS WT", f"{slip['gross']:.2f} T", value_font=mono)
    row("TARE WT", f"{slip['tare']:.2f} T", value_font=mono)

    # The net weight is the number the whole bill rests on, so it is boxed.
    draw.rectangle([MARGIN, y - 8, WIDTH - MARGIN, y + 46], outline=(20, 20, 20), width=3)
    draw.text((MARGIN + 14, y + 6), "NET WT", font=label, fill=(20, 20, 20))
    draw.text((MARGIN + 250, y + 2), f"{slip['net']:.2f} T", font=font(32), fill=(10, 10, 10))
    y += 74

    draw.line([MARGIN, y, WIDTH - MARGIN, y], fill=(120, 120, 120), width=2)
    y += 28

    row("TIME IN", slip["timeIn"], value_font=mono)
    row("TIME OUT", slip["timeOut"], value_font=mono)

    y += 40
    draw.text((MARGIN, y), "OPERATOR", font=label, fill=(70, 70, 70))
    draw.line([MARGIN + 250, y + 30, MARGIN + 520, y + 30], fill=(60, 60, 60), width=2)
    y += 70

    draw.text(
        (WIDTH / 2, HEIGHT - MARGIN - 28),
        "Simulated for the SiltProof demo. Not a real weighment.",
        font=small,
        fill=(120, 120, 120),
        anchor="mm",
    )
    draw.text(
        (WIDTH / 2, HEIGHT - MARGIN - 4),
        f"trip {trip['tripId']}",
        font=small,
        fill=(170, 170, 170),
        anchor="mm",
    )

    return image


def degrade(image, random):
    """Make the slip look photographed on a phone, badly."""
    # A soft shadow across one corner, as if a hand got in the way.
    shadow = Image.new("L", image.size, 0)
    shadow_draw = ImageDraw.Draw(shadow)
    corner = random.choice(["left", "right", "top"])
    if corner == "left":
        shadow_draw.polygon([(0, 0), (int(WIDTH * 0.35), 0), (0, HEIGHT)], fill=90)
    elif corner == "right":
        shadow_draw.polygon([(WIDTH, 0), (WIDTH, HEIGHT), (int(WIDTH * 0.6), HEIGHT)], fill=80)
    else:
        shadow_draw.polygon([(0, 0), (WIDTH, 0), (WIDTH, int(HEIGHT * 0.2)), (0, int(HEIGHT * 0.3))], fill=70)
    shadow = shadow.filter(ImageFilter.GaussianBlur(70))
    image = Image.composite(Image.new("RGB", image.size, (40, 40, 40)), image, shadow)

    # A slight perspective warp, as if the phone was not held square.
    skew = random.uniform(0.004, 0.012) * random.choice([-1, 1])
    image = image.transform(
        image.size,
        Image.Transform.AFFINE,
        (1, skew, -skew * HEIGHT / 2, skew / 2, 1, 0),
        resample=Image.Resampling.BICUBIC,
        fillcolor=(235, 235, 232),
    )

    image = image.rotate(
        random.uniform(-2.8, 2.8), resample=Image.Resampling.BICUBIC, fillcolor=(235, 235, 232)
    )
    image = image.filter(ImageFilter.GaussianBlur(random.uniform(0.5, 0.9)))

    return image


def choose_degraded(trips, share, seed):
    """Pick which slips get degraded, deterministically."""
    random = ds.rng(seed, "degrade")
    eligible = [trip["tripId"] for trip in trips if trip["drainId"] not in CRISP_DRAINS]
    count = int(len(eligible) * share)
    return set(random.sample(eligible, count)) if count else set()


def select_trips(trips, limit):
    """All trips, or the planted ones plus a spread of clean ones."""
    if not limit or limit >= len(trips):
        return trips

    planted = [trip for trip in trips if trip["drainId"] in ds.PLANTED]
    rest = [trip for trip in trips if trip["drainId"] not in ds.PLANTED]

    chosen = planted[:limit]
    if len(chosen) < limit:
        step = max(1, len(rest) // (limit - len(chosen)))
        chosen.extend(rest[::step][: limit - len(chosen)])

    chosen.sort(key=lambda trip: (int(trip["drainId"]), trip["tripNo"]))
    return chosen


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trips", default=None, help="trips.json (default data/out/trips.json)")
    parser.add_argument("--limit", type=int, default=0, help="render only this many slips")
    parser.add_argument("--degrade-share", type=float, default=DEGRADE_SHARE)
    parser.add_argument("--seed", default="siltproof")
    args = parser.parse_args(argv)

    trips_path = pathlib.Path(args.trips or (ds.OUT / "trips.json"))
    if not trips_path.exists():
        print(f"No trips file at {ds.relative(trips_path)}.")
        print("Run data/gen_trips.py first: a slip has to agree with its trip.")
        return 2

    trips = ds.load_json(trips_path)["trips"]
    chosen = select_trips(trips, args.limit)
    degraded = choose_degraded(chosen, args.degrade_share, args.seed)

    ds.banner("SiltProof weighbridge slips")
    print(f"weighbridge {WEIGHBRIDGE_NAME} (fictional)")
    print(f"slips       {len(chosen)} of {len(trips)} trips")
    print(f"degraded    {len(degraded)} (drain {'/'.join(sorted(CRISP_DRAINS))} always crisp)")

    manifest_path = ds.OUT / "mock_manifest.json"
    manifest = ds.load_json(manifest_path) if manifest_path.exists() else {}
    manifest.setdefault("slips", {})

    out_root = ds.OUT / "evidence"
    for trip in chosen:
        image = draw_slip(trip)
        is_degraded = trip["tripId"] in degraded

        if is_degraded:
            image = degrade(image, random_module.Random(f"{args.seed}:{trip['tripId']}"))

        path = out_root / trip["slipKey"]
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format="PNG", optimize=True)

        entry = dict(manifest["slips"].get(trip["slipKey"], {}))
        entry.update(trip["slip"])
        entry["augmented"] = is_degraded
        manifest["slips"][trip["slipKey"]] = entry

    ds.save_json(manifest_path, manifest)

    print(f"\nwrote       {len(chosen)} slips under {ds.relative(out_root)}/slips")
    print(f"manifest    {ds.relative(manifest_path)}")
    print("\nPrint 8-10 of these, photograph them, and drop the photos in over the")
    print("generated PNGs to show Textract reading real paper.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
