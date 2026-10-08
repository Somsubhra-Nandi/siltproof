#!/usr/bin/env node
/**
 * Screenshot the offline dashboard, so the map can be checked without a
 * person looking at a browser.
 *
 *   npm run screenshot
 *
 * Builds nothing and starts nothing by itself: point it at a running server.
 *   npm run preview -- --port 4173     (or npm run dev)
 *   npm run screenshot
 *
 * Drives the Chrome already installed on this machine through puppeteer-core,
 * so nothing is downloaded. MapLibre needs WebGL, which headless Chrome only
 * has through SwiftShader, hence the flags below.
 *
 * Writes frontend/screenshots/*.png.
 */

import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import puppeteer from 'puppeteer-core'

const HERE = dirname(fileURLToPath(import.meta.url))
const OUT = join(HERE, '..', 'screenshots')

const BASE = process.env.SCREENSHOT_URL ?? 'http://localhost:4173'
const CHROME =
  process.env.CHROME_PATH ??
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

// Scale 1: these are committed, and four retina PNGs came to 3 MB. Still
// plenty to read the panel text at 1440x900.
const VIEWPORT = { width: 1440, height: 900, deviceScaleFactor: 1 }

// Software WebGL: headless Chrome has no GPU, and without these MapLibre
// never paints a thing.
const ARGS = [
  '--headless=new',
  '--no-sandbox',
  '--disable-dev-shm-usage',
  '--use-gl=angle',
  '--use-angle=swiftshader',
  '--enable-unsafe-swiftshader',
  '--disable-lcd-text',
  '--hide-scrollbars',
]

const SHOTS = [
  {
    name: '1-before-verification',
    path: '/',
    note: 'drains grey, only the claim on the bar',
    async ready(page) {
      await page.waitForSelector('button.verify:not([disabled])')
      await waitForMap(page)
    },
  },
  {
    name: '2-verified',
    path: '/',
    note: 'Run Verification pressed: drains coloured, money split',
    async ready(page) {
      await page.waitForSelector('button.verify:not([disabled])')
      await waitForMap(page)
      await page.click('button.verify')
      await page.waitForFunction(
        () => document.querySelector('button.verify')?.textContent?.includes('Re-run'),
        { timeout: 20000 },
      )
      // Let the count-up and the colour-in finish.
      await sleep(2500)
    },
  },
  {
    name: '4-fallback-after-style-failure',
    path: '/?state=verified&style=/deliberately-missing-style.json',
    note: 'Amazon Location style fails to load: the map swaps to the offline one',
    async ready(page) {
      await page.waitForSelector('.map-mode', { timeout: 20000 })
      await waitForMap(page)
      await sleep(2500)
    },
  },
  {
    name: '3-drain-14',
    path: '/?state=verified&drain=14',
    note: 'the hero case: both routes and the evidence panel',
    async ready(page) {
      await page.waitForSelector('.panel h2')
      await waitForMap(page)
      await sleep(2500)
    },
  },
]

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** Wait until MapLibre has actually painted something onto its canvas. */
async function waitForMap(page) {
  await page.waitForSelector('canvas.maplibregl-canvas', { timeout: 20000 })
  await page.waitForFunction(
    () => {
      const canvas = document.querySelector('canvas.maplibregl-canvas')
      if (!canvas || canvas.width === 0) return false
      const gl =
        canvas.getContext('webgl2', { preserveDrawingBuffer: true }) ??
        canvas.getContext('webgl', { preserveDrawingBuffer: true })
      return Boolean(gl)
    },
    { timeout: 20000 },
  )
  await sleep(1800)
}

async function main() {
  await mkdir(OUT, { recursive: true })

  const browser = await puppeteer.launch({ executablePath: CHROME, args: ARGS })
  const notes = []

  try {
    for (const shot of SHOTS) {
      const page = await browser.newPage()
      await page.setViewport(VIEWPORT)

      const problems = []
      page.on('console', (event) => {
        if (event.type() === 'error') problems.push(event.text())
      })
      page.on('pageerror', (error) => problems.push(String(error)))

      const url = `${BASE}${shot.path}`
      process.stdout.write(`${shot.name}: ${url}\n`)

      await page.goto(url, { waitUntil: 'networkidle2', timeout: 30000 })
      await shot.ready(page)

      const file = join(OUT, `${shot.name}.png`)
      await page.screenshot({ path: file })

      const mode = await page.$eval('.map-mode', (node) => node.textContent).catch(() => null)
      notes.push({ name: shot.name, note: shot.note, mode, problems })

      process.stdout.write(
        `  saved ${file}  (${mode ? mode.trim() : 'Amazon Location'})\n` +
          (problems.length ? `  console errors: ${problems.length}\n` : ''),
      )
      await page.close()
    }
  } finally {
    await browser.close()
  }

  await writeFile(
    join(OUT, 'README.md'),
    [
      '# Screenshots',
      '',
      'Taken by `npm run screenshot` against the offline snapshot, with no AWS',
      'and no Amazon Location key, so the map is the bundled fallback style.',
      'Regenerate them rather than editing them.',
      '',
      ...notes.map(
        (entry) =>
          `- **${entry.name}.png** — ${entry.note}` +
          (entry.mode ? ` _(${entry.mode.trim()})_` : ''),
      ),
      '',
    ].join('\n'),
  )

  const failed = notes.filter((entry) => entry.problems.length)
  if (failed.length) {
    process.stdout.write('\nConsole errors were logged:\n')
    for (const entry of failed) {
      process.stdout.write(`  ${entry.name}: ${entry.problems.slice(0, 3).join(' | ')}\n`)
    }
  }
}

main().catch((cause) => {
  process.stderr.write(`${cause?.stack ?? cause}\n`)
  process.exit(1)
})
