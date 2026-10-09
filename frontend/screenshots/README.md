# Screenshots

Captured from the real React app, not the prototype, at 1920x1080 against
the offline snapshot. There is no AWS and no Amazon Location key, so the map
is the bundled fallback style. Regenerate them rather than editing them:

```bash
npm run build && npx vite preview --port 4173 --strictPort
npm run screenshot        # stills into screenshots/app
npm run capture           # stills and the two videos in ../videos
```

`app/` follows the DESIGN.md list (`01` to `08`, plus the exhibit
close-ups). It adds `09-drain-*` (other drains through the same case file),
`10`, `10b` and `11` (1440x900 and 1280x800), and `12` (390 px wide).

`location-mock/` is written by `npm run check:location`: the Amazon Location
path against a test double, then the fallback after a 403. It shows wiring,
not real tiles. See `docs/amazon-location.md`.
