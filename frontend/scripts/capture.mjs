#!/usr/bin/env node
/**
 * Screenshots and two videos of the real React app, at 1920x1080, from the
 * offline snapshot. The counterpart of design/capture-hybrid.mjs, which
 * captured the prototype.
 *
 *   npm run build && npx vite preview --port 4173 --strictPort   (one terminal)
 *   node scripts/capture.mjs [stills|video|all]                   (another)
 *
 * Every state is reached by driving the UI the way an engineer would:
 * clicks, typing, Escape. Nothing is injected into the page. Uses the
 * installed Chrome through puppeteer-core with SwiftShader for WebGL, and
 * Playwright's ffmpeg (if installed) to encode the screencast.
 *
 * Writes frontend/screenshots/app/*.png and frontend/videos/*.webm.
 */
import { spawn } from 'node:child_process'
import { existsSync, readdirSync } from 'node:fs'
import { mkdir } from 'node:fs/promises'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import puppeteer from 'puppeteer-core'

const HERE = dirname(fileURLToPath(import.meta.url))
const SHOTS = join(HERE, '..', 'screenshots', 'app')
const VIDS = join(HERE, '..', 'videos')
const BASE = process.env.BASE ?? 'http://localhost:4173'
const CHROME = process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const ARGS = ['--headless=new', '--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--hide-scrollbars', '--disable-lcd-text']
const PW = join(homedir(), 'Library/Caches/ms-playwright')
const FFMPEG =
  process.env.FFMPEG ??
  (existsSync(PW) ? join(PW, readdirSync(PW).find((name) => name.startsWith('ffmpeg')) ?? 'ffmpeg', 'ffmpeg-mac') : 'ffmpeg')
const what = process.argv[2] ?? 'all'

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function open(browser, path, size = { width: 1920, height: 1080 }) {
  const page = await browser.newPage()
  await page.setViewport({ ...size, deviceScaleFactor: 1 })
  page.on('pageerror', (error) => console.error(`  page error on ${path}: ${error.message}`))
  await page.goto(BASE + path)
  await page.waitForSelector('.ring-marker', { timeout: 30000 })
  await page.evaluate(() => document.fonts.ready)
  await sleep(1800)
  return page
}

const clickText = async (page, selector, text) => {
  const handles = await page.$$(selector)
  for (const handle of handles) {
    const label = await handle.evaluate((el) => el.textContent ?? '')
    if (label.includes(text)) return handle.click()
  }
  throw new Error(`no ${selector} with "${text}"`)
}

async function shot(page, name, closeups = []) {
  await page.screenshot({ path: join(SHOTS, `${name}.png`) })
  for (const [selector, file] of closeups) {
    const el = await page.$(selector)
    if (el) await el.screenshot({ path: join(SHOTS, `${file}.png`) })
  }
  console.log(`  ${name}`)
}

async function approveReview(page, drainId) {
  await page.click(`.row[data-drain="${drainId}"]`)
  await sleep(250)
  await page.click('.inline .btn-primary')
  await sleep(300)
  await page.click('.confirm [data-confirm]')
  await sleep(1100)
}

async function stills(browser) {
  let page = await open(browser, '/')
  await shot(page, '01-pre-verification')
  await page.close()

  page = await open(browser, '/?state=verified')
  await shot(page, '02-verified')
  await page.hover('.row[data-drain="3"]')
  await sleep(500)
  await shot(page, '08-hover')
  await page.close()

  page = await open(browser, '/?state=verified&drain=14')
  await sleep(1200)
  await shot(page, '03-drain-14', [
    ['.exA', 'closeup-exhibit-a-route'],
    ['[data-shot="slip"]', 'closeup-exhibit-b-slip'],
    ['[data-shot="photos"]', 'closeup-exhibit-c-photos'],
  ])
  await clickText(page, '.decide .btn', 'Hold ₹')
  await sleep(400)
  await shot(page, '04-hold-confirmation')
  await page.keyboard.press('Escape')
  await sleep(200)
  await clickText(page, '.decide .btn', 'Approve and release')
  await sleep(400)
  await shot(page, '05b-approve-confirmation-drain-14')
  await page.keyboard.press('Escape')
  await page.type('#decision-note', 'Every trace stops 2.2 km short of the dump site. Slip stamped before departure.')
  await clickText(page, '.decide .btn', 'Hold ₹')
  await sleep(300)
  await page.click('.confirm [data-confirm]')
  await sleep(150)
  await shot(page, '05c-decision-saving')
  await sleep(1200)
  await shot(page, '06b-drain-14-held')
  await page.close()

  page = await open(browser, '/?state=verified&drain=14&photos=slot')
  await sleep(1200)
  await shot(page, '03b-drain-14-real-photo-slot', [['[data-shot="photos"]', 'closeup-exhibit-c-photo-slot']])
  await page.close()

  page = await open(browser, '/?state=verified&drain=14&simulate=save-error')
  await sleep(1200)
  await clickText(page, '.decide .btn', 'Hold ₹')
  await sleep(300)
  await page.click('.confirm [data-confirm]')
  await sleep(1200)
  await shot(page, '05d-decision-error')
  await page.close()

  page = await open(browser, '/?state=verified')
  await page.click('.row[data-drain="8"]')
  await sleep(250)
  await page.click('.inline .btn-primary')
  await sleep(400)
  await shot(page, '05-approve-confirmation-review-drain')
  await page.click('.confirm [data-confirm]')
  await sleep(1100)
  await approveReview(page, '6')
  // Hold drain 14 from its case file, then come back to the bill.
  await page.click('.row[data-drain="14"]')
  await page.waitForSelector('.decide .btn-held', { timeout: 20000 })
  await sleep(1500)
  await clickText(page, '.decide .btn', 'Hold ₹')
  await sleep(300)
  await page.click('.confirm [data-confirm]')
  await sleep(1200)
  await page.click('.back')
  await sleep(1800)
  await shot(page, '06-decision-completed')
  await page.close()

  for (const id of ['3', '6', '11', '16', '1']) {
    page = await open(browser, `/?state=verified&drain=${id}`)
    await sleep(1200)
    await shot(page, `09-drain-${id}`)
    await page.close()
  }

  for (const [name, size, path] of [
    ['10-overview-1440x900', { width: 1440, height: 900 }, '/?state=verified'],
    ['10b-drain-14-1440x900', { width: 1440, height: 900 }, '/?state=verified&drain=14'],
    ['11-drain-14-1280x800', { width: 1280, height: 800 }, '/?state=verified&drain=14'],
    ['12-overview-390x844', { width: 390, height: 844 }, '/?state=verified'],
  ]) {
    page = await open(browser, path, size)
    await sleep(1200)
    await page.screenshot({ path: join(SHOTS, `${name}.png`), fullPage: false })
    console.log(`  ${name}`)
    await page.close()
  }
}

/** CDP screencast, re-timed to 25 fps and encoded as VP9 WebM. */
async function record(page, file, act) {
  const cdp = await page.createCDPSession()
  const frames = []
  cdp.on('Page.screencastFrame', async (frame) => {
    frames.push({ t: frame.metadata.timestamp, data: Buffer.from(frame.data, 'base64') })
    await cdp.send('Page.screencastFrameAck', { sessionId: frame.sessionId }).catch(() => {})
  })
  await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 90, maxWidth: 1920, maxHeight: 1080 })
  await act()
  await cdp.send('Page.stopScreencast')
  const FPS = 25
  const t0 = frames[0].t
  const end = frames[frames.length - 1].t
  const ff = spawn(FFMPEG, ['-loglevel', 'error', '-y', '-f', 'image2pipe', '-r', String(FPS), '-c:v', 'mjpeg', '-i', 'pipe:0', '-c:v', 'libvpx-vp9', '-b:v', '2M', '-deadline', 'realtime', '-cpu-used', '8', '-row-mt', '1', '-pix_fmt', 'yuv420p', file])
  let i = 0
  for (let k = 0; t0 + k / FPS <= end; k++) {
    while (i + 1 < frames.length && frames[i + 1].t <= t0 + k / FPS) i++
    if (!ff.stdin.write(frames[i].data)) await new Promise((resolve) => ff.stdin.once('drain', resolve))
  }
  ff.stdin.end()
  await new Promise((resolve, reject) => ff.on('close', (code) => (code === 0 ? resolve() : reject(new Error(`ffmpeg ${code}`)))))
  console.log(`  ${file.split('/').pop()}: ${(end - t0).toFixed(1)} s, ${frames.length} frames`)
}

async function videos(browser) {
  let page = await open(browser, '/')
  await record(page, join(VIDS, '01-verification-scan.webm'), async () => {
    await sleep(600)
    await page.click('button.verify')
    await page.waitForSelector('.reran', { timeout: 30000 })
    await sleep(1600)
  })
  await page.close()

  page = await open(browser, '/?state=verified')
  await record(page, join(VIDS, '02-open-drain-14.webm'), async () => {
    await page.hover('.row[data-drain="14"]')
    await sleep(600)
    await page.click('.row[data-drain="14"]')
    await page.waitForSelector('.casefile .decide .btn-held', { timeout: 30000 })
    await sleep(2400)
  })
  await page.close()
}

await mkdir(SHOTS, { recursive: true })
await mkdir(VIDS, { recursive: true })
const browser = await puppeteer.launch({ executablePath: CHROME, args: ARGS })
try {
  if (what !== 'video') await stills(browser)
  if (what !== 'stills') await videos(browser)
} finally {
  await browser.close()
}
