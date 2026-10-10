# Replacing the generated photographs with licensed ones

The 18-drain case study (Bill B1) uses photos drawn by `data/gen_photos.py`:
geometric stand-ins, deliberately varied so their perceptual hashes stay far
apart. This page is the route to licensed, realistic photographs **without**
changing a verdict, a total or the evidence relationships the rules test.

Nothing here runs by default. The generated photos stay the fallback: build
the snapshot without the flags below and you get exactly what is committed.

## What must not change

| Relationship | Why | How it is kept |
|---|---|---|
| A before and an after photo per drain, a load photo for drains 3 and 9 | the slots R1 to R4 read | one CSV row per slot, 38 rows, all required |
| Drain 14 `after-02` is an exact copy of drain 9 `after-01` | R3, hard fail, Hamming 0 | rebuilt from the new drain 9 photo: same encoded pixels, new EXIF |
| Drain 14 `after-03` is a cropped, brightened copy | R3 catches edited reuse | rebuilt with the generator's crop and 1.18 brightness; must stay at or under 12 bits |
| No other pair within 12 bits | R3 would flag a photo that is not a copy | every pair is checked; 16 bits or fewer is refused |
| Drain 3's load is construction debris | R4, hard fail | the slot keeps `loadType: debris`; the photo must show rubble |
| Drain 8's after photo is 45 m outside its geofence | R1 soft band, drain 8 amber | the slot keeps its simulated GPS |
| Every photo inside the work window | R2 | the slot keeps its simulated time |

The totals (claimed 1,240 t; verified 805 t; review 65 t; held 370 t =
₹6.66 lakh; 870 t after the two approvals) come from the slots' tags and the
manifest, not from pixels, so they do not move. `tests/test_demo_snapshot.py`
and the ground-truth tests confirm it after a rebuild.

## Location and time tags: a decision to take first

A stock photograph was not taken at a SiltProof drain. The import:

1. **discards** the photograph's own EXIF (camera, GPS, time); none of it is
   copied;
2. writes the slot's **simulated** GPS and time, the same tags the generated
   photo carries, so R1 and R2 still run;
3. writes an `ImageDescription` saying the tags are simulated and naming the
   author, licence and source page, and `Artist` and `Copyright` fields.

That still puts GPS tags on a third-party photograph, so the import refuses to
write unless `--accept-simulated-tags` is passed. The alternative, no tags,
would turn every photo check into a missing-evidence review and change the
bill's totals. The UI already says, under every set of photos: *"Generated
stand-in images. Their GPS and time tags belong to the simulated dataset."*
With licensed photographs that line should change to name them as licensed
illustrative photographs (see "Still to do").

Never mix in the Kolkata field photographs (`docs/KOLKATA-FIELD-EVIDENCE.md`):
they are real evidence from another place, not illustrations of Mumbai drains.

## The photographs to source

- **38 photographs**: 18 silted or clogged *before*, 18 cleared *after*, one
  tipper load or heap of wet black silt (drain 9), one heap of construction
  debris: brick, concrete, tiles (drain 3).
- **Subject**: Indian urban open storm-water drains (nullahs), ideally
  concrete-lined rectangular channels. Before: silt banks, plastic and
  garbage, black standing water. After: visible bed and invert, desilted
  channel.
- **Pairs**: the same style of drain within a drain's before and after where
  possible. Across drains, vary the composition (no burst series from one
  spot), or the hash check will refuse near-duplicates.
- **Size**: landscape, at least 1600 x 1200. Each is centre-cropped to 4:3
  and saved at 1200 x 900, JPEG quality 85.
- **No** faces, readable number plates, brand logos or recognisable landmarks.
- **Licences accepted**: CC0, Public Domain Mark, CC BY 2.0/3.0/4.0, Unsplash
  License, Pexels License. **Refused**: ND (the crop is a derivative), NC, SA,
  agency photos (Getty, Alamy, news), anything without a stated licence.

## The sources file

Copy `data/photo_sources.example.csv`. One row per slot:

| Column | Required | Meaning |
|---|---|---|
| `file` | yes | file name inside `--photos-dir` |
| `drainId`, `role` | yes | `1`-`18`; `before`, `after` or `load` |
| `sourceUrl` | yes | the page the photograph came from |
| `author` | yes | as the licence asks to credit them |
| `licence` | yes | one of the accepted codes above |
| `licenceUrl` | no | the licence text |
| `observation` | no | what the photo shows, used as its pre-written reading; keep drain 3's saying debris |
| `note` | no | free text, ignored |

Drain 14's `after-02` and `after-03` have no rows: they are rebuilt.

## Steps

```bash
# 1. Check the sources and the photographs. Writes nothing.
python data/import_photos.py --sources data/photo_sources.csv --photos-dir ~/siltproof-licensed

# 2. Rebuild the offline snapshot with them (offline, no AWS).
python scripts/make_demo_fixtures.py \
    --photo-sources data/photo_sources.csv --photos-dir ~/siltproof-licensed \
    --accept-simulated-tags

# 3. Verify nothing but the photos moved.
python -m pytest
cd frontend && npm test
```

Step 2 also writes `frontend/public/data/demo/photo-credits.json`: each photo's
source, author, licence and pHash, and for drain 14's copies the photo they
were made from. Expect the drain JSON to change only in `pHash` values and,
where an `observation` was given, the photo's pre-written notes.

Keep the licensed originals and the filled sources file out of git unless the
licence allows redistribution.

## Still to do before licensed photos are published

1. **Show the credits.** CC BY needs visible attribution. The frontend does
   not read `photo-credits.json` yet: add a credit line per photo (in the
   enlarged view) and a credits list.
2. **Change the photo footnote** to say the photographs are licensed
   illustrative photographs, not taken at these drains, with simulated
   location and time tags.
3. Take the decision on simulated tags above, and record it in DECISIONS.md.
