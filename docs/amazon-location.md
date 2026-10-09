# Amazon Location basemap

The production demo draws the drains, traces and evidence over a real Amazon
Location Service map (Maps v2, **Monochrome, Light**), tinted to the survey
sheet. The bundled offline map is a fallback, used automatically when Amazon
Location is not configured or does not answer.

Nothing here has been tested against real AWS tiles yet. The wiring has been
tested with a mocked endpoint (below). Real tiles count as verified only once
they have loaded in a browser with a real key.

## How the app uses it

- The browser fetches
  `https://maps.geo.<region>.amazonaws.com/v2/styles/<style>/descriptor?key=<key>&color-scheme=<scheme>`.
  The style descriptor then points MapLibre at the tiles, glyphs and sprites.
- The only secret in the browser is a **Location API key**. There are no AWS
  access keys, secret keys or IAM credentials in the frontend. The key is
  public by nature, so it must be scoped (below).
- Once the style loads, `surveyTint` (`frontend/src/basemap.ts`) recolours the
  background to limestone `#E3DED2` and water layers to `#AEC0C3`. Roads and
  labels keep Amazon's styling. All overlays, including drains, rings, GPS
  trails, the dump-site geofence, labels and the camera moves, are drawn
  above it.
- The MapLibre attribution control shows Amazon's data attribution. It is a
  licence requirement, so keep it visible.
- **Fallback:** the offline style is used when the key or region is missing,
  when the style request fails (for example 403 or a CORS error), or when it
  has not loaded after 6 s. A small "Offline basemap" note then appears on the
  map.

## Configuration

These are build-time Vite variables. Set them in `frontend/.env` for local
work, or as Amplify environment variables.

| Variable | Default | Meaning |
|---|---|---|
| `VITE_AWS_REGION` | none | Region of the key, e.g. `ap-south-1` |
| `VITE_LOCATION_API_KEY` | none | The Location API key. Without it the offline map is used |
| `VITE_LOCATION_MAP_STYLE` | `Monochrome` | `Monochrome`, `Standard`, `Hybrid` or `Satellite` |
| `VITE_LOCATION_COLOR_SCHEME` | `Light` | `Light` or `Dark` |

`?style=<url>` in the page URL still overrides the style, as a development aid.

## Creating the API key (manual, needs your approval)

1. In the Amazon Location console, open **API keys** and choose **Create API key**,
   in the same region as `VITE_AWS_REGION`.
2. Under resources and actions, grant only **Maps**. The descriptor, tiles,
   glyphs and sprites are separate actions: `geo-maps:GetStyleDescriptor`,
   `geo-maps:GetTile`, `geo-maps:GetGlyphs` and `geo-maps:GetSprites`. Do not
   grant Places, Routes or anything else to this key. Check the action names
   the console offers on the day; labels need glyphs and sprites.
3. **Restrict referrers** so the key only works from the app's own origins:
   - `https://main.<amplify-app-id>.amplifyapp.com/*` (the Amplify branch URL)
   - your custom domain, if any
   - `http://localhost:5173/*` and `http://localhost:4173/*` for local dev and
     preview (remove these after the demo if you prefer)
4. Set an expiry date after the hackathon.
5. Put the key in `frontend/.env` as `VITE_LOCATION_API_KEY` (git-ignored).
   Never commit it.

## Amplify Hosting

- App root `frontend`, build command `npm ci && npm run build`, output `dist`.
- Under **Environment variables**, add `VITE_AWS_REGION`,
  `VITE_LOCATION_API_KEY`, `VITE_LOCATION_MAP_STYLE` (optional),
  `VITE_LOCATION_COLOR_SCHEME` (optional) and `VITE_API_BASE_URL`. Vite bakes
  them in at build time, so **redeploy after changing any of them**.
- The app is a single page with query-string state (`?drain=14`), so it needs
  no rewrite rules beyond Amplify's default.
- Without `VITE_API_BASE_URL`, the hosted app runs on the offline snapshot,
  which is still a complete demo.

## Verifying

**Mocked, no AWS (done):**

```bash
cd frontend
VITE_AWS_REGION=ap-south-1 VITE_LOCATION_API_KEY=mock-key npx vite --port 5182 --strictPort
node scripts/check-location-mock.mjs        # in a second terminal
```

The script intercepts every request to `maps.geo.*.amazonaws.com` in headless
Chrome and answers with a test-double style. Nothing else leaves the machine.
It checks that the app asks for the Monochrome Light v2 descriptor with only
the API key, keeps the Amazon basemap with attribution and all 18 drains, and
falls back to the offline map on a 403. Screenshots go to
`frontend/screenshots/location-mock/`.

**Real tiles (after the key exists, with your approval):**

1. Run `npm run dev` with the real key in `frontend/.env`.
2. Open `http://localhost:5173/?state=verified`. The network panel should show
   `GetStyleDescriptor` and tile requests answered with 200, and the map should
   show Amazon's roads and labels in the survey tint. There should be no
   "Offline basemap" note, and the attribution (ⓘ) should name Amazon's data
   providers.
3. Open `?drain=14` and confirm the trail, dump-site hatch and labels draw
   over the tiles.
4. Repeat on the Amplify URL once deployed. A 403 there usually means the
   referrer list is missing that origin.

Each map view makes billable Amazon Location tile requests. The demo's volume
is small, but it is not zero.

## What is simulated

The basemap is real geography (Mumbai, near the Mithi river). The 18 drains,
the contractor, the GPS traces, the dump-site geofence, the slips and the
photos are generated. The app labels them "Simulated data", and drawing them
on real tiles does not make them observations.
