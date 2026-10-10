# Photo mapping: real photos -> drains

`data/gen_photos.py` writes stand-in photos so the pipeline can be built and
tested before the shoot. After the shoot, the real photos take over and nothing
else in the pipeline changes.

For licensed stock photographs in place of the generated ones (not a real
shoot), use `data/import_photos.py` instead: see `docs/PHOTO-REPLACEMENT.md`.

## What to do after the photo shoot

1. Move the originals off the phone **by cable, AirDrop or Google Drive**.
   WhatsApp strips EXIF, and without EXIF there is no GPS and no timestamp, so
   rules R1 and R2 cannot run (plan section 6).
2. Blur faces and real number plates.
3. Put every photo in one folder, any filenames.
4. Fill in `data/photo_map.csv` (copy `data/photo_map.example.csv`).
5. Seed with the real photos instead of the generated ones:

   ```bash
   python data/seed.py --photos-dir ~/siltproof-photos \
                       --photo-map data/photo_map.csv --live
   ```

Check the EXIF survived before you build on it:

```bash
python data/check_photos.py ~/siltproof-photos --drains data/out/drains.geojson
```

That prints GPS, timestamp and which drain geofence each photo falls inside,
and flags anything with no EXIF.

## The file

`data/photo_map.csv`, one row per photo, with a header:

```csv
filename,drainId,role,note
IMG_4471.jpg,14,before,
IMG_4472.jpg,14,after,
IMG_4480.jpg,3,load,construction debris pile
IMG_4481.jpg,9,load,silt heap
```

| Column | Required | Meaning |
|---|---|---|
| `filename` | yes | The file's name inside `--photos-dir`. Not a path. |
| `drainId` | yes | `1`–`18`, matching `drainId` in `drains.geojson`. |
| `role` | yes | `before`, `after` or `load`. |
| `note` | no | Free text, kept in DynamoDB for the drill-down panel. |

Rows whose file is missing are reported and skipped; files in the folder with
no row are ignored. Blank lines and `#` comments are allowed.

## Where each photo ends up

```
photos/B1/drain<drainId>/<role>-<nn>.jpg
```

`<nn>` is assigned in file order per drain and role, starting at 01. That is
the key convention the ingest Lambda parses, so the drain and role reach
DynamoDB without any extra lookup.

## Keeping the planted cases

The demo story needs the cases in plan section 6. Two of them are photo-based,
so they need rows in the mapping:

- **Drain 3** needs a `load` photo of a **construction debris** pile. Bedrock
  has to see rubble, not silt, or the drain will not turn red.
- **Drain 14** needs its after photo to be a **reuse of drain 9's after
  photo**. Map the same file to both drains:

  ```csv
  IMG_4495.jpg,9,after,
  IMG_4495.jpg,14,after,reused on purpose - R3 hero case
  ```

  `seed.py` uploads it twice under two keys, which is exactly what pHash should
  catch. For the edited-reuse case, also add a cropped and brightened copy of
  the same photo as a second drain 14 `after` row.

- **Drain 8** needs one photo whose GPS is 30–60 m outside its geofence, so the
  drain reads amber rather than red. Easiest in the field: stand across the
  road from that drain for one shot.

If a planted case is missing after seeding, `data/out/ground_truth.json` will
disagree with what the rules produce, and the Day 2 test will say so.
