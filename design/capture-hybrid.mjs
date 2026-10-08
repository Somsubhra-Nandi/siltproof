#!/usr/bin/env node
/**
 * Screenshots and motion previews of the approved hybrid, at exactly
 * 1920x1080, device scale factor 1.
 *
 *   python3 -m http.server 8765 --directory design      (in one terminal)
 *   node design/capture-hybrid.mjs [stills|video|all]
 *
 * Same tooling as capture.mjs: the installed Chrome with SwiftShader for
 * WebGL, CDP screencast frames encoded by Playwright's own ffmpeg.
 */
import { mkdir } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import { spawn } from 'node:child_process'
import { homedir } from 'node:os'
import { readdirSync, writeFileSync } from 'node:fs'

const HERE = dirname(fileURLToPath(import.meta.url))
const SHOTS = join(HERE, 'screenshots', 'hybrid')
const VIDS = join(HERE, 'videos', 'hybrid')
const BASE = (process.env.BASE ?? 'http://localhost:8765') + '/hybrid/'
const SIZE = { width: 1920, height: 1080 }
const CHROME = process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const ARGS = ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--hide-scrollbars', '--disable-lcd-text']
const PW_CACHE = join(homedir(), 'Library/Caches/ms-playwright')
const FFMPEG = process.env.FFMPEG ?? join(PW_CACHE, readdirSync(PW_CACHE).find((n) => n.startsWith('ffmpeg')) ?? 'ffmpeg', 'ffmpeg-mac')
const what = process.argv[2] ?? 'all'
const only = process.argv[3]

// [file, query, close-ups as [selector, file]]
const STILLS = [
  ['01-pre-verification', '?view=pre'],
  ['02-verified', '?view=verified'],
  ['03-drain-14', '?view=drain14', [['[data-shot="slip"]', 'closeup-exhibit-b-slip'], ['[data-shot="photos"]', 'closeup-exhibit-c-photos'], ['#exA', 'closeup-exhibit-a-route']]],
  ['03b-drain-14-real-photo-slot', '?view=drain14&photos=slot', [['[data-shot="photos"]', 'closeup-exhibit-c-photo-slot']]],
  ['04-hold-confirmation', '?view=drain14&confirm=hold'],
  ['05-approve-confirmation-review-drain', '?view=verified&review=8&confirm=approve'],
  ['05b-approve-confirmation-drain-14', '?view=drain14&confirm=approve'],
  ['05c-decision-saving', '?view=drain14&saving=1'],
  ['05d-decision-error', '?view=drain14&error=1'],
  ['06-decision-completed', '?view=verified&approved=6,8&held14=1'],
  ['06b-drain-14-held', '?view=drain14&decided=hold'],
  ['07-components', '?view=system'],
  ['08-hover', '?view=verified&hover=3'],
]
const VIDEOS = [
  ['01-verification-scan', '?view=pre&play=scan'],
  ['02-open-drain-14', '?view=verified&play=open14'],
]

const browser = await chromium.launch({ executablePath: CHROME, args: ARGS, headless: true })
await mkdir(SHOTS, { recursive: true }); await mkdir(VIDS, { recursive: true })
const ready = async (page) => { await page.waitForFunction(() => window.__ready === true, null, { timeout: 60000 }); await page.evaluate(() => document.fonts.ready) }

if (what !== 'video') {
  const ctx = await browser.newContext({ viewport: SIZE, deviceScaleFactor: 1 })
  const page = await ctx.newPage()
  page.on('pageerror', (e) => console.error('page error:', e.message))
  for (const [name, q, closeups = []] of STILLS) {
    if (only && !name.startsWith(only)) continue
    await page.goto(BASE + q)
    await ready(page)
    await page.waitForTimeout(q.includes('drain14') ? 2200 : 900)
    await page.screenshot({ path: join(SHOTS, `${name}.png`) })
    for (const [sel, file] of closeups) await page.locator(sel).first().screenshot({ path: join(SHOTS, `${file}.png`) })
    console.log('still', name)
  }
  await ctx.close()
}
if (what !== 'stills') {
  for (const [name, q] of VIDEOS) {
    if (only && !name.startsWith(only)) continue
    const ctx = await browser.newContext({ viewport: SIZE, deviceScaleFactor: 1 })
    const page = await ctx.newPage()
    page.on('pageerror', (e) => console.error('page error:', e.message))
    await page.goto(BASE + q + '&wait=1')
    await ready(page)
    await page.waitForTimeout(800)
    const secs = await record(page, async () => {
      await page.evaluate(() => window.__go())
      await page.waitForFunction(() => window.__done === true, null, { timeout: 90000 })
      await page.waitForTimeout(900)
    }, join(VIDS, `${name}.webm`))
    await ctx.close()
    console.log(`video ${name}, ${secs.toFixed(1)} s`)
  }
}
await browser.close()

/** Screencast from the first drawn frame, re-timed to 25 fps, VP9. */
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
  const FPS = 25, t0 = frames[0].t, end = frames.at(-1).t
  if (process.env.DEBUG) for (const q of [0.1, 0.25, 0.4, 0.55, 0.7, 0.85, 1]) writeFileSync(join(process.env.DEBUG, file.split('/').pop() + '-' + q + '.jpg'), frames[Math.min(frames.length - 1, Math.floor(q * frames.length))].data)
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
