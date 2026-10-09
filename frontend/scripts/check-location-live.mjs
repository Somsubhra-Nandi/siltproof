// Real Amazon Location tiles, checked in headless Chrome. Billable: one style
// descriptor plus the tiles, glyphs and sprites for a few map views.
//
// The offline fallback proves nothing about the live map, so this loads a
// build that has VITE_LOCATION_API_KEY baked in and checks what really came
// back from maps.geo.<region>.amazonaws.com:
//
//   VITE_LOCATION_API_KEY=v1.public... VITE_AWS_REGION=ap-south-1 npm run build
//   npx vite preview --port 4173 --strictPort        # 4173 must be an allowed referrer
//   CHROME_PATH=... node scripts/check-location-live.mjs --live
//
// Without --live it prints the plan and exits. Screenshots go to
// screenshots/location-live/ (look at them: roads, water and labels should be
// visible under the survey tint).

import { mkdirSync } from 'node:fs'
import { join, resolve } from 'node:path'
import puppeteer from 'puppeteer-core'

const live = process.argv.includes('--live')
const base = process.env.APP_URL ?? 'http://localhost:4173'
const out = resolve('screenshots/location-live')

console.log(`Amazon Location live check against ${base}`)
console.log('  billable: 1 style descriptor and the tiles, glyphs and sprites for 3 views')
if (!live) {
  console.log('\nDry run. Re-run with --live once the key exists and the build includes it.')
  process.exit(0)
}
mkdirSync(out, { recursive: true })

const failures = []
const expect = (ok, what) => {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${what}`)
  if (!ok) failures.push(what)
}
const sleep = (ms) => new Promise((done) => setTimeout(done, ms))

const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_PATH,
  headless: true,
  args: ['--no-sandbox', '--use-gl=swiftshader', '--enable-unsafe-swiftshader'],
})
const amazon = { descriptor: [], tile: [], glyph: [], sprite: [], other: [] }
let descriptor = null
const tileZooms = []
const consoleErrors = []

try {
  const page = await browser.newPage()
  await page.setViewport({ width: 1440, height: 900 })
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()) })
  page.on('response', async (response) => {
    const url = response.url()
    if (!/^https:\/\/maps\.geo\.[a-z0-9-]+\.amazonaws\.com\//.test(url)) return
    const kind = /\/descriptor/.test(url) ? 'descriptor' : /\/tiles?\//.test(url) ? 'tile'
      : /\/glyphs\//.test(url) ? 'glyph' : /\/sprites\//.test(url) ? 'sprite' : 'other'
    amazon[kind].push(response.status())
    const zoom = url.match(/\/tiles\/[^/]+\/(\d+)\//)
    if (zoom) tileZooms.push(Number(zoom[1]))
    if (kind === 'descriptor' && response.ok()) descriptor = await response.json().catch(() => null)
  })

  for (const [name, path] of [['overview', '/?state=verified'], ['drain-14', '/?state=verified&drain=14']]) {
    await page.goto(base + path, { waitUntil: 'networkidle2', timeout: 60000 })
    await sleep(name === 'drain-14' ? 7000 : 3000)
    await page.screenshot({ path: join(out, `${name}.png`) })
    if (name !== 'overview') continue

    // Zoom in and pan: new tiles at a deeper zoom must come back too.
    const box = await (await page.$('[data-testid="map"]')).boundingBox()
    const cx = box.x + box.width / 2
    const cy = box.y + box.height / 2
    const before = { count: amazon.tile.length, maxZoom: Math.max(...tileZooms) }
    await page.mouse.move(cx + 120, cy - 120)
    for (let step = 0; step < 4; step++) { await page.mouse.wheel({ deltaY: -400 }); await sleep(250) }
    await sleep(2500)
    await page.mouse.down()
    await page.mouse.move(cx - 80, cy + 60, { steps: 12 })
    await page.mouse.up()
    await sleep(2500)
    await page.screenshot({ path: join(out, 'overview-zoomed-panned.png') })
    const fresh = amazon.tile.slice(before.count)
    expect(fresh.length > 0 && fresh.every((status) => status === 200 || status === 304),
      `zoom and pan load new tiles (${fresh.length}, all 200)`)
    expect(Math.max(...tileZooms) > before.maxZoom, `zoom reaches deeper tiles (z${before.maxZoom} -> z${Math.max(...tileZooms)})`)
    const fellBack = await page.evaluate(() => /Offline basemap/.test(document.body.innerText))
    expect(!fellBack, 'still the Amazon map after zoom and pan (no switch to the offline basemap)')
  }

  // A slow connection: tiles still loading after the 6 s style timer must not
  // be mistaken for a failed style (MapLibre's isStyleLoaded() counts tiles).
  const slow = await browser.newPage()
  await slow.setViewport({ width: 1440, height: 900 })
  await slow.emulateNetworkConditions({ download: 60 * 1024, upload: 30 * 1024, latency: 400 })
  await slow.goto(base + '/?state=verified', { waitUntil: 'domcontentloaded', timeout: 120000 })
  await sleep(14000)
  const slowFellBack = await slow.evaluate(() => /Offline basemap/.test(document.body.innerText))
  expect(!slowFellBack, 'slow connection: still the Amazon map 14 s in (no false fallback)')
  await slow.screenshot({ path: join(out, 'overview-slow-network.png') })
  await slow.close()
  await page.setViewport({ width: 390, height: 844 })
  await page.goto(base + '/?state=verified', { waitUntil: 'networkidle2', timeout: 60000 })
  await sleep(3000)
  await page.screenshot({ path: join(out, 'overview-390.png'), fullPage: true })

  const text = await page.evaluate(() => document.body.innerText)
  const attribution = await page.$eval('.maplibregl-ctrl-attrib', (el) => el.textContent ?? '').catch(() => '')

  const allOk = (list) => list.length > 0 && list.every((status) => status === 200 || status === 204 || status === 304)
  expect(allOk(amazon.descriptor), `style descriptor 200 (${amazon.descriptor.join(', ')})`)
  expect(allOk(amazon.tile), `tiles 200 (${amazon.tile.length} requests, ${amazon.tile.filter((s) => s >= 400).length} failed)`)
  expect(allOk(amazon.glyph), `glyphs 200 (${amazon.glyph.length})`)
  expect(amazon.sprite.length === 0 || allOk(amazon.sprite), `sprites 200 (${amazon.sprite.length})`)
  expect(!/Offline basemap/i.test(text), 'no "Offline basemap" note: the fallback did not engage')
  expect(/amazon|here|openstreetmap|esri|grab/i.test(attribution), `provider attribution shown ("${attribution.trim().slice(0, 80)}")`)
  const layers = descriptor?.layers ?? []
  const roads = layers.filter((layer) => /road|street|highway|motorway|path/i.test(`${layer.id} ${layer['source-layer'] ?? ''}`))
  const water = layers.filter((layer) => /water|river|lake|ocean|sea|canal/i.test(`${layer.id} ${layer['source-layer'] ?? ''}`))
  const labels = layers.filter((layer) => layer.type === 'symbol')
  expect(roads.length > 0, `descriptor has road layers (${roads.length}), left untinted`)
  expect(water.length > 0, `descriptor has water layers (${water.length}), tinted to the survey palette`)
  expect(labels.length > 0, `descriptor has label layers (${labels.length})`)
  expect(consoleErrors.length === 0, `no console errors${consoleErrors.length ? ': ' + consoleErrors.slice(0, 3).join(' | ') : ''}`)
  if (amazon.descriptor.some((s) => s === 403)) {
    console.log('\n403 on the descriptor: check the key allows geo-maps actions on')
    console.log('arn:aws:geo-maps:<region>::provider/default and lists this origin as a referrer.')
  }
} catch (error) {
  failures.push(String(error))
  console.log('FAIL', error)
} finally {
  await browser.close()
}

const total = Object.values(amazon).reduce((sum, list) => sum + list.length, 0)
console.log(`\nAmazon Location requests: ${total} (descriptor ${amazon.descriptor.length}, tiles ${amazon.tile.length}, glyphs ${amazon.glyph.length}, sprites ${amazon.sprite.length}, other ${amazon.other.length})`)
console.log(`screenshots in ${out}`)
console.log(failures.length ? `${failures.length} FAILED` : 'live map checks passed')
process.exit(failures.length ? 1 : 0)
