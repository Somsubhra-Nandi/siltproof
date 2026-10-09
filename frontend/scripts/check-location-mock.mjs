#!/usr/bin/env node
/**
 * Exercise the Amazon Location basemap path without touching AWS.
 *
 *   VITE_AWS_REGION=ap-south-1 VITE_LOCATION_API_KEY=mock-key \
 *     npx vite --port 5182 --strictPort            (in one terminal)
 *   node scripts/check-location-mock.mjs          (in another)
 *
 * Every request to maps.geo.*.amazonaws.com is intercepted in the browser
 * and answered with a small test-double style, so no AWS call is made and
 * nothing is billed. It checks three things:
 *   1. the app asks for the configured Maps v2 style (Monochrome, Light);
 *   2. the survey tint is applied and our layers draw above the basemap,
 *      with the provider attribution shown;
 *   3. when Amazon Location refuses (403), the offline fallback takes over.
 *
 * This proves the wiring, not real tiles. Real tiles are proven only by
 * loading the app with a real key and seeing them in a browser.
 */
import { mkdir } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import puppeteer from 'puppeteer-core'

const HERE = dirname(fileURLToPath(import.meta.url))
const OUT = join(HERE, '..', 'screenshots', 'location-mock')
const BASE = process.env.BASE ?? 'http://localhost:5182'
const CHROME = process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const ARGS = ['--headless=new', '--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--hide-scrollbars']

// The real service answers browser requests with CORS headers; so must the double.
const CORS = { 'Access-Control-Allow-Origin': '*' }
const MOCK_ATTRIBUTION = 'Test double for Amazon Location (no AWS call)'

function mockStyle() {
  return {
    version: 8,
    name: 'Mock Monochrome Light',
    sources: {
      // Stands in for Amazon's vector tiles with the app's own backdrop file.
      mock: { type: 'geojson', data: `${BASE}/data/basemap.geojson`, attribution: MOCK_ATTRIBUTION },
    },
    layers: [
      { id: 'Background', type: 'background', paint: { 'background-color': '#ffffff' } },
      { id: 'Water', type: 'fill', source: 'mock', filter: ['==', ['get', 'kind'], 'water'], paint: { 'fill-color': '#0000ff' } },
      { id: 'Roads', type: 'line', source: 'mock', filter: ['==', ['get', 'kind'], 'road'], paint: { 'line-color': '#d0d0d0', 'line-width': 2 } },
    ],
  }
}

async function run(name, respond) {
  const browser = await puppeteer.launch({ executablePath: CHROME, args: ARGS })
  const page = await browser.newPage()
  await page.setViewport({ width: 1920, height: 1080 })
  page.on('console', (message) => {
    if (message.text().includes('[siltproof]') || message.type() === 'error') console.log(`     ${name}: ${message.text()}`)
  })
  await page.setRequestInterception(true)
  const asked = []
  page.on('request', (request) => {
    const url = request.url()
    if (/^https:\/\/maps\.geo\.[a-z0-9-]+\.amazonaws\.com\//.test(url)) {
      asked.push(url)
      respond(request)
    } else if (/^https?:\/\/(localhost|127\.0\.0\.1)/.test(url) || url.startsWith('data:') || url.startsWith('blob:') || /fonts\.(googleapis|gstatic)\.com/.test(url)) {
      request.continue()
    } else {
      // Nothing else may leave the machine.
      request.abort()
    }
  })
  await page.goto(`${BASE}/?state=verified`)
  await new Promise((resolve) => setTimeout(resolve, 6000))
  const state = await page.evaluate(() => ({
    fallbackBadge: Boolean(document.querySelector('.map-mode')),
    attribution: document.querySelector('.maplibregl-ctrl-attrib')?.textContent ?? '',
    rings: document.querySelectorAll('.ring-marker').length,
  }))
  await page.screenshot({ path: join(OUT, `${name}.png`) })
  await browser.close()
  return { asked, ...state }
}

await mkdir(OUT, { recursive: true })
let failed = 0
const check = (ok, text) => {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${text}`)
  if (!ok) failed++
}

const live = await run('1-mock-amazon-location', (request) =>
  request.respond({ status: 200, headers: CORS, contentType: 'application/json', body: JSON.stringify(mockStyle()) }),
)
check(live.asked.some((url) => url.includes('/v2/styles/Monochrome/descriptor') && url.includes('color-scheme=Light')), 'asks for the Monochrome Light Maps v2 descriptor')
check(live.asked.every((url) => !/secret|X-Amz-Credential|AWSAccessKeyId/i.test(url)), 'sends only the API key, no AWS credentials')
check(!live.fallbackBadge, 'stays on the Amazon Location basemap')
check(live.attribution.includes(MOCK_ATTRIBUTION), 'shows the provider attribution')
check(live.rings === 18, 'draws all 18 drains above the basemap')

const refused = await run('2-amazon-location-refused', (request) =>
  request.respond({ status: 403, headers: CORS, contentType: 'application/json', body: '{"message":"Forbidden"}' }),
)
check(refused.fallbackBadge, 'falls back to the offline basemap when Amazon Location refuses')
check(refused.rings === 18, 'still draws all 18 drains on the fallback')

console.log(failed ? `\n${failed} check(s) failed` : `\nAll checks passed. Screenshots in ${OUT}`)
process.exit(failed ? 1 : 0)
