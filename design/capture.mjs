#!/usr/bin/env node
/**
 * Screenshots and short videos of the three design prototypes, at exactly
 * 1920x1080, device scale factor 1 - the recording size.
 *
 *   python3 -m http.server 8765 --directory design      (in one terminal)
 *   node design/capture.mjs [1|2|3|all] [stills|video|all]
 *
 * Videos need Playwright's ffmpeg: npx playwright install ffmpeg
 *
 * Needs Playwright (not a dependency of the app). Drives the installed Chrome,
 * with SwiftShader for WebGL, so nothing is downloaded.
 */
import { mkdir } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { spawn } from 'node:child_process'
import { homedir } from 'node:os'
import { readdirSync, writeFileSync } from 'node:fs'

const HERE = dirname(fileURLToPath(import.meta.url))
const OUT = join(HERE, 'screenshots')
const BASE = process.env.BASE ?? 'http://localhost:8765'
const SIZE = { width: 1920, height: 1080 }
const CHROME = process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const ARGS = ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--hide-scrollbars', '--disable-lcd-text']

const PW_CACHE = join(homedir(), 'Library/Caches/ms-playwright')
const FFMPEG = process.env.FFMPEG ?? join(PW_CACHE, readdirSync(PW_CACHE).find((n) => n.startsWith('ffmpeg')) ?? 'ffmpeg', 'ffmpeg-mac')
const which = process.argv[2] ?? '1'
const what = process.argv[3] ?? 'all'
const dirs = which === 'all' ? ['1', '2', '3'] : [which]

const STILLS = [
  ['1-pre-verification', '?view=pre'],
  ['2-verified', '?view=verified'],
  ['3-drain-14', '?view=drain14'],
  ['4-hover', '?view=verified&hover=3'],
  ['5-confirm-hold', '?view=drain14&confirm=hold'],
  ['6-held', '?view=drain14&decided=1'],
  ['7-components', '?view=system'],
]
const VIDEOS = [
  ['scan', '?view=pre&play=scan'],
  ['open-drain-14', '?view=verified&play=open14'],
]

const browser = await chromium.launch({ executablePath: CHROME, args: ARGS, headless: true })
await mkdir(OUT, { recursive: true })

async function ready(page) {
  await page.waitForFunction(() => window.__ready === true, null, { timeout: 60000 })
  await page.evaluate(() => document.fonts.ready)
}

for (const d of dirs) {
  if (what !== 'video') {
    const ctx = await browser.newContext({ viewport: SIZE, deviceScaleFactor: 1 })
    const page = await ctx.newPage()
    page.on('pageerror', (e) => console.error(`[${d}] page error:`, e.message))
    for (const [name, q] of STILLS) {
      await page.goto(`${BASE}/direction-${d}/${q}`)
      await ready(page)
      await page.waitForTimeout(q.includes('drain14') ? 2500 : 900)
      await page.screenshot({ path: join(OUT, `${d}-${name}.png`) })
      if (name === '3-drain-14') {
        const el = page.locator('[data-shot="evidence"]').first()
        if (await el.count()) await el.screenshot({ path: join(OUT, `${d}-closeup-evidence.png`) })
      }
      console.log(`direction ${d}: ${name}`)
    }
    await ctx.close()
  }
  if (what !== 'stills') {
    for (const [name, q] of VIDEOS) {
      const ctx = await browser.newContext({ viewport: SIZE, deviceScaleFactor: 1 })
      const page = await ctx.newPage()
      page.on('pageerror', (e) => console.error(`[${d}] page error:`, e.message))
      await page.goto(`${BASE}/direction-${d}/${q}&wait=1`)
      await ready(page)
      await page.waitForTimeout(800)
      const secs = await record(page, async () => {
        await page.evaluate(() => window.__go())
        await page.waitForFunction(() => window.__done === true, null, { timeout: 60000 })
        await page.waitForTimeout(1500)
      }, join(OUT, `${d}-${name}.webm`))
      await ctx.close()
      console.log(`direction ${d}: video ${name}, ${secs.toFixed(1)} s`)
    }
  }
}

/**
 * Record from the moment the page is ready, not from page creation, so the
 * video carries no blank loading frames. CDP screencast frames are re-timed
 * onto a 25 fps grid and encoded with Playwright's own ffmpeg build.
 */
async function record(page, during, file) {
  const cdp = await page.context().newCDPSession(page)
  const frames = []
  cdp.on('Page.screencastFrame', async (f) => {
    frames.push({ t: f.metadata.timestamp, data: Buffer.from(f.data, 'base64') })
    await cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }).catch(() => {})
  })
  await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 90, maxWidth: SIZE.width, maxHeight: SIZE.height })
  await during()
  await cdp.send('Page.stopScreencast')
  const FPS = 25, t0 = frames[0].t, end = frames[frames.length - 1].t
  if (process.env.DEBUG) for (const q of [0.2, 0.4, 0.6, 0.8, 1]) writeFileSync(join(process.env.DEBUG, file.split('/').pop() + '-' + q + '.jpg'), frames[Math.min(frames.length - 1, Math.floor(q * frames.length))].data)
  const ff = spawn(FFMPEG, ['-loglevel', 'error', '-y', '-f', 'image2pipe', '-r', String(FPS), '-c:v', 'mjpeg', '-i', 'pipe:0', '-c:v', 'libvpx-vp9', '-b:v', '2M', '-deadline', 'realtime', '-cpu-used', '8', '-row-mt', '1', '-pix_fmt', 'yuv420p', file])
  let i = 0
  for (let k = 0; t0 + k / FPS <= end; k++) {
    while (i + 1 < frames.length && frames[i + 1].t <= t0 + k / FPS) i++
    if (!ff.stdin.write(frames[i].data)) await new Promise((r) => ff.stdin.once('drain', r))
  }
  ff.stdin.end()
  await new Promise((r, j) => ff.on('close', (c) => (c === 0 ? r() : j(new Error('ffmpeg ' + c)))))
  return end - t0
}
await browser.close()
