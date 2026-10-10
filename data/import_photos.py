#!/usr/bin/env python3
"""Check, and optionally apply, licensed photographs in place of the generated ones.

    python data/import_photos.py --sources data/photo_sources.csv --photos-dir <folder>

The generated photos (data/gen_photos.py) are geometric stand-ins. This swaps
their pixels for licensed photographs supplied with a source, author and
licence for each one, while every relationship the rules depend on stays put:

  * each replacement takes the slot (drain and role) of a generated photo;
  * drain 14's two reused after-photos are rebuilt from the new drain 9
    after-photo, one an exact copy and one cropped and brightened, as the
    generator makes them, so R3 still catches both;
  * perceptual hashes are checked before anything is accepted: the copies
    must stay within R3's threshold of their source, and every other pair
    must stay well clear of it, or R3 would flag photos that are not copies.

Location and time. A licensed photograph was not taken at a SiltProof drain.
Its own EXIF (camera, GPS, time) is discarded and never copied. Rules R1 and
R2 need a location and a time, so the slot's *simulated* tags, the ones the
generated photo already carries, are written onto the replacement, with an
ImageDescription saying they are simulated and naming the source and licence.
Because that writes GPS tags onto a third-party photograph, it only happens
with --accept-simulated-tags; see docs/PHOTO-REPLACEMENT.md.

With no --work-dir this only validates the sources file and the photographs.
scripts/make_demo_fixtures.py calls apply() on its own working folder when
given --photo-sources, so nothing under data/out or the committed snapshot
changes unless the snapshot is rebuilt on purpose. Offline: no AWS.
"""

import argparse
import csv
import io
import itertools
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import dataset as ds  # noqa: E402

import imagehash  # noqa: E402
import piexif  # noqa: E402
from PIL import Image, ImageEnhance, ImageOps  # noqa: E402

from common.rules import PHASH_DUPLICATE_MAX  # noqa: E402

# What the replacement is saved as: 4:3, large enough to read when enlarged.
SIZE = (1200, 900)
MIN_SOURCE = (1600, 1200)
QUALITY = 85
# Unrelated photos must differ by at least this many bits more than R3 allows.
UNRELATED_MARGIN = 4

# Licences that allow cropping, re-encoding and use in a public demo without
# share-alike or non-commercial terms. Anything else is refused.
ALLOWED_LICENCES = {
    "CC0-1.0": "Creative Commons Zero",
    "PDM-1.0": "Public Domain Mark",
    "CC-BY-2.0": "Creative Commons Attribution 2.0",
    "CC-BY-3.0": "Creative Commons Attribution 3.0",
    "CC-BY-4.0": "Creative Commons Attribution 4.0",
    "Unsplash": "Unsplash License",
    "Pexels": "Pexels License",
}
NEEDS_CREDIT = {"CC-BY-2.0", "CC-BY-3.0", "CC-BY-4.0"}

COLUMNS = ["file", "drainId", "role", "sourceUrl", "author", "licence"]
OPTIONAL = ["licenceUrl", "observation", "note"]


class PhotoImportError(Exception):
    """A sources file or photograph that cannot be accepted, with every reason."""

    def __init__(self, problems):
        super().__init__("\n".join(problems))
        self.problems = problems


# --------------------------------------------------------------- the slots
def slots(manifest):
    """Photo slots a replacement fills, and the derived copies rebuilt from them.

    Returns ({(drainId, role): key}, {derived key: (source key, reuseKind)}).
    """
    fill, derived = {}, {}
    for key, meta in manifest["photos"].items():
        if meta.get("reusedFrom"):
            derived[key] = (meta["reusedFrom"], meta.get("reuseKind", "exact copy"))
        else:
            fill[(str(meta["drainId"]), meta["role"])] = key
    return fill, derived


def read_sources(path):
    with open(path, newline="", encoding="utf-8") as handle:
        rows = [
            row for row in csv.DictReader(line for line in handle if not line.lstrip().startswith("#"))
            if any((value or "").strip() for value in row.values())
        ]
    return [{key: (value or "").strip() for key, value in row.items()} for row in rows]


def validate_rows(rows, fill):
    """Every slot filled once, by a row with a source, an author and an allowed licence."""
    problems = []
    seen = {}
    for number, row in enumerate(rows, start=2):
        where = f"row {number} ({row.get('file') or 'no file'})"
        missing = [column for column in COLUMNS if not row.get(column)]
        if missing:
            problems.append(f"{where}: missing {', '.join(missing)}")
            continue
        if row["licence"] not in ALLOWED_LICENCES:
            problems.append(
                f"{where}: licence {row['licence']!r} is not accepted "
                f"(allowed: {', '.join(sorted(ALLOWED_LICENCES))})"
            )
        if not row["sourceUrl"].startswith(("https://", "http://")):
            problems.append(f"{where}: sourceUrl must be the page the photograph came from")
        slot = (row["drainId"], row["role"])
        if slot not in fill:
            problems.append(f"{where}: no generated photo for drain {slot[0]} role {slot[1]!r}")
        elif slot in seen:
            problems.append(f"{where}: drain {slot[0]} {slot[1]} is already filled by row {seen[slot]}")
        else:
            seen[slot] = number
    for slot in sorted(set(fill) - set(seen), key=lambda s: (int(s[0]), s[1])):
        problems.append(f"no photograph for drain {slot[0]} {slot[1]}")
    files = [row.get("file") for row in rows if row.get("file")]
    for name in sorted({name for name in files if files.count(name) > 1}):
        problems.append(f"{name} is used for more than one slot; each slot needs its own photograph")
    return problems


def validate_images(rows, photos_dir, minimum=MIN_SOURCE):
    problems = []
    for row in rows:
        path = pathlib.Path(photos_dir) / row["file"]
        if not path.is_file():
            problems.append(f"{row['file']}: not found in {photos_dir}")
            continue
        try:
            with Image.open(path) as image:
                width, height = ImageOps.exif_transpose(image).size
        except OSError as exc:
            problems.append(f"{row['file']}: not a readable image ({exc})")
            continue
        if width < minimum[0] or height < minimum[1]:
            problems.append(f"{row['file']}: {width}x{height}, needs at least {minimum[0]}x{minimum[1]}")
        if width < height:
            problems.append(f"{row['file']}: portrait; the exhibits need a landscape photograph")
    return problems


# ----------------------------------------------------------------- pixels
def fit(image, size=SIZE):
    """Upright, RGB, centre-cropped to 4:3 and resized. Metadata is not kept."""
    image = ImageOps.exif_transpose(image).convert("RGB")
    return ImageOps.fit(image, size, method=Image.Resampling.LANCZOS)


def edited_copy(image):
    """The cropped and brightened reuse, scaled from gen_photos' 900x675 crop."""
    width, height = image.size
    dx, dy = round(26 * width / 900), round(20 * height / 675)
    cropped = image.crop((dx, dy, width - dx, height - dy)).resize(image.size)
    return ImageEnhance.Brightness(cropped).enhance(1.18)


def simulated_exif(generated_path, row):
    """The generated photo's simulated tags, labelled, with the source named."""
    exif = piexif.load(str(generated_path))
    description = (
        f"SiltProof case study. Photograph: {row['author']}, {row['licence']}, {row['sourceUrl']}. "
        "GPS and time tags are simulated for the case study; this is not where or when it was taken."
    )
    exif["0th"][piexif.ImageIFD.ImageDescription] = description.encode("utf-8")
    exif["0th"][piexif.ImageIFD.Artist] = row["author"].encode("utf-8")
    exif["0th"][piexif.ImageIFD.Copyright] = f"{row['author']} ({row['licence']})".encode("utf-8")
    exif["0th"][piexif.ImageIFD.Software] = b"siltproof import_photos.py"
    exif["thumbnail"] = None
    exif["1st"] = {}
    return piexif.dump(exif)


def encode(image, exif_bytes):
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=QUALITY, optimize=True)
    out = io.BytesIO()
    piexif.insert(exif_bytes, buffer.getvalue(), out)
    return out.getvalue()


def phash_of(data):
    with Image.open(io.BytesIO(data)) as image:
        return imagehash.phash(ImageOps.exif_transpose(image))


def check_hashes(hashes, derived):
    """R3 must see the copies as copies, and nothing else as one."""
    problems = []
    pairs = {(copy, source) for copy, (source, _) in derived.items()}
    for copy, (source, kind) in derived.items():
        distance = hashes[copy] - hashes[source]
        limit = 0 if kind == "exact copy" else PHASH_DUPLICATE_MAX
        if distance > limit:
            problems.append(f"{copy} is {distance} bits from {source}; R3 needs {limit} or fewer")
    for a, b in itertools.combinations(sorted(hashes), 2):
        if (a, b) in pairs or (b, a) in pairs:
            continue
        # Two copies of the same source are related to each other too.
        if a in derived and b in derived and derived[a][0] == derived[b][0]:
            continue
        distance = hashes[a] - hashes[b]
        if distance <= PHASH_DUPLICATE_MAX + UNRELATED_MARGIN:
            problems.append(
                f"{a} and {b} are only {distance} bits apart; R3 calls {PHASH_DUPLICATE_MAX} or fewer "
                "a copy. Choose a more different photograph for one of them."
            )
    return problems


def apply(work_dir, sources, photos_dir, accept_simulated_tags=False):
    """Replace the generated photos under work_dir/evidence with the licensed ones.

    Nothing is written unless every check passes. Returns the credits list.
    """
    work_dir = pathlib.Path(work_dir)
    if not accept_simulated_tags:
        raise PhotoImportError([
            "Refusing to write: replacements carry the case study's simulated GPS and time tags. "
            "Pass --accept-simulated-tags once that is agreed (docs/PHOTO-REPLACEMENT.md)."
        ])
    manifest_path = work_dir / "mock_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    evidence = work_dir / "evidence"
    fill, derived = slots(manifest)
    rows = read_sources(sources)
    problems = validate_rows(rows, fill) + validate_images(rows, photos_dir)
    if problems:
        raise PhotoImportError(problems)

    staged = {}
    by_key = {}
    for row in rows:
        key = fill[(row["drainId"], row["role"])]
        with Image.open(pathlib.Path(photos_dir) / row["file"]) as source:
            image = fit(source)
        staged[key] = encode(image, simulated_exif(evidence / key, row))
        by_key[key] = (row, image)
    for key, (source_key, kind) in derived.items():
        row, image = by_key[source_key]
        tags = simulated_exif(evidence / key, row)
        if kind == "exact copy":
            # Same encoded pixels as the source; only the EXIF differs.
            out = io.BytesIO()
            piexif.insert(tags, staged[source_key], out)
            staged[key] = out.getvalue()
        else:
            staged[key] = encode(edited_copy(image), tags)

    hashes = {key: phash_of(data) for key, data in staged.items()}
    problems = check_hashes(hashes, derived)
    if problems:
        raise PhotoImportError(problems)

    credits = []
    for key, data in sorted(staged.items()):
        (evidence / key).write_bytes(data)
        source_key = derived.get(key, (key,))[0]
        row = by_key[source_key][0]
        meta = manifest["photos"][key]
        if row.get("observation") and key == source_key:
            meta["notes"] = row["observation"]
        credits.append({
            "key": key,
            "drainId": meta["drainId"],
            "role": meta["role"],
            "derivedFrom": None if key == source_key else source_key,
            "file": row["file"],
            "sourceUrl": row["sourceUrl"],
            "author": row["author"],
            "licence": row["licence"],
            "licenceName": ALLOWED_LICENCES[row["licence"]],
            "licenceUrl": row.get("licenceUrl") or None,
            "creditRequired": row["licence"] in NEEDS_CREDIT,
            "pHash": str(hashes[key]),
        })
    # Copies describe their source, as the generator's copies do.
    for key, (source_key, _) in derived.items():
        manifest["photos"][key]["notes"] = manifest["photos"][source_key]["notes"]
    ds.save_json(manifest_path, manifest)
    return credits


def encode_plain(image):
    """JPEG with no EXIF at all: no camera, no GPS, no time."""
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=QUALITY, optimize=True)
    return buffer.getvalue()


def _walk_evidence(node):
    """Every finding evidence dict inside a drain record."""
    if isinstance(node, dict):
        if isinstance(node.get("evidence"), dict):
            yield node["evidence"]
        for value in node.values():
            yield from _walk_evidence(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_evidence(value)


def apply_to_snapshot(snapshot_dir, sources, photos_dir, manifest):
    """Display-only: put the licensed pixels into the committed offline snapshot.

    The snapshot's evidence records (GPS, time, verdicts, decisions) are left as
    the prepared investigation wrote them. Only what is derived from pixels moves
    with the pixels: each photo's pHash, the R3 Hamming distances, and the
    pre-written observation where the sources file gives one. The JPEGs carry no
    EXIF. Refuses to write unless R3 still finds exactly the same copies.
    Returns the credits list.
    """
    from common.photo import perceptual_hash
    from common.rules import find_duplicates

    snapshot_dir = pathlib.Path(snapshot_dir)
    fill, derived = slots(manifest)
    rows = read_sources(sources)
    problems = validate_rows(rows, fill) + validate_images(rows, photos_dir)
    if problems:
        raise PhotoImportError(problems)

    staged, by_key = {}, {}
    for row in rows:
        key = fill[(row["drainId"], row["role"])]
        with Image.open(pathlib.Path(photos_dir) / row["file"]) as source:
            image = fit(source)
        staged[key] = encode_plain(image)
        by_key[key] = (row, image)
    for key, (source_key, kind) in derived.items():
        staged[key] = staged[source_key] if kind == "exact copy" else encode_plain(edited_copy(by_key[source_key][1]))

    hashes = {key: perceptual_hash(data) for key, data in staged.items()}
    problems = check_hashes({key: imagehash.hex_to_hash(h) for key, h in hashes.items()}, derived)

    paths = sorted(snapshot_dir.glob("drain-*.json"))
    drains = {path: json.loads(path.read_text(encoding="utf-8")) for path in paths}
    photos = [photo for drain in drains.values() for photo in drain["photos"]]
    missing = sorted({photo["s3Key"] for photo in photos} ^ set(staged))
    if missing:
        problems.append(f"snapshot photos and sources disagree on: {', '.join(missing)}")
    if problems:
        raise PhotoImportError(problems)

    # R3 must find the same copies of the same originals with the new hashes.
    before = {key: dup["original"] for key, dup in find_duplicates(photos).items()}
    after_photos = [dict(photo, pHash=hashes[photo["s3Key"]]) for photo in photos]
    after = find_duplicates(after_photos)
    if before != {key: dup["original"] for key, dup in after.items()}:
        raise PhotoImportError([f"R3 would change: {before} became {after}"])

    notes = {key: row["observation"] for key, (row, _) in by_key.items() if row.get("observation")}
    for key, (source_key, _) in derived.items():
        if source_key in notes:
            notes[key] = notes[source_key]
    for drain in drains.values():
        for photo in drain["photos"]:
            photo["pHash"] = hashes[photo["s3Key"]]
            if photo["s3Key"] in notes and photo.get("bedrock"):
                photo["bedrock"]["notes"] = notes[photo["s3Key"]]
        for evidence in _walk_evidence(drain):
            key = evidence.get("s3Key")
            if key in after and "hammingDistance" in evidence:
                evidence["hammingDistance"] = after[key]["distance"]
            if key in notes and "notes" in evidence:
                evidence["notes"] = notes[key]

    # Everything checked: write.
    for key, data in staged.items():
        (snapshot_dir / "evidence" / key).write_bytes(data)
    for path, drain in drains.items():
        path.write_text(json.dumps(drain, indent=1) + "\n")

    credits = []
    for key in sorted(staged):
        source_key = derived.get(key, (key,))[0]
        row = by_key[source_key][0]
        credits.append({
            "key": key, "derivedFrom": None if key == source_key else source_key,
            "sourceUrl": row["sourceUrl"], "author": row["author"], "licence": row["licence"],
            "pHash": hashes[key],
        })
    return credits


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sources", required=True, help="CSV, see data/photo_sources.example.csv")
    parser.add_argument("--photos-dir", required=True, help="folder holding the photographs")
    parser.add_argument("--manifest", default=str(ds.OUT / "mock_manifest.json"),
                        help="generator manifest naming the slots (default data/out/mock_manifest.json)")
    parser.add_argument("--snapshot", help="display-only: write the photos into this offline snapshot "
                        "(frontend/public/data/demo) without EXIF; see docs/PHOTO-REPLACEMENT.md")
    args = parser.parse_args(argv)

    manifest = json.loads(pathlib.Path(args.manifest).read_text())
    if args.snapshot:
        try:
            credits = apply_to_snapshot(args.snapshot, args.sources, args.photos_dir, manifest)
        except PhotoImportError as exc:
            print(f"Nothing written. {len(exc.problems)} problem(s):")
            for problem in exc.problems:
                print(f"  - {problem}")
            return 1
        print(f"Wrote {len(credits)} photos into {args.snapshot}; R3 finds the same copies.")
        return 0
    fill, derived = slots(manifest)
    rows = read_sources(args.sources)
    problems = validate_rows(rows, fill) + validate_images(rows, args.photos_dir)
    if not problems:
        hashes = {}
        for row in rows:
            with Image.open(pathlib.Path(args.photos_dir) / row["file"]) as source:
                hashes[fill[(row["drainId"], row["role"])]] = imagehash.phash(fit(source))
        for key, (source_key, kind) in derived.items():
            with Image.open(pathlib.Path(args.photos_dir) / next(
                r["file"] for r in rows if fill[(r["drainId"], r["role"])] == source_key
            )) as source:
                image = fit(source)
            hashes[key] = imagehash.phash(image if kind == "exact copy" else edited_copy(image))
        problems = check_hashes(hashes, derived)

    print(f"{len(rows)} photographs for {len(fill)} slots; {len(derived)} copies rebuilt from them")
    if problems:
        print(f"\n{len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("Every slot is filled, every licence is accepted and the perceptual hashes keep R3's cases.")
    print("Rebuild the snapshot with: python scripts/make_demo_fixtures.py "
          "--photo-sources <csv> --photos-dir <folder> --accept-simulated-tags")
    return 0


if __name__ == "__main__":
    sys.exit(main())
