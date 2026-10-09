// Drives trial.html end to end in headless Chrome against the offline trial
// dev server (scripts/trial_dev_server.py). Nothing reaches AWS.
//
//   .venv/bin/python scripts/trial_dev_server.py &
//   .venv/bin/python scripts/make_trial_fixtures.py /tmp/trial-fixtures
//   cd frontend && VITE_API_BASE_URL=http://127.0.0.1:3001 npx vite --port 5173 &
//   CHROME_PATH=/path/to/chrome node scripts/check-trial.mjs /tmp/trial-fixtures
//
// Writes screenshots to screenshots/trial/ and exits non-zero on any failed
// expectation or browser console error.

import { mkdirSync } from 'node:fs'
import { join, resolve } from 'node:path'
import puppeteer from 'puppeteer-core'

const fixtures = resolve(process.argv[2] ?? '/tmp/trial-fixtures')
const base = process.env.TRIAL_URL ?? 'http://localhost:5173/trial.html'
const out = resolve('screenshots/trial')
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
const page = await browser.newPage()
const consoleErrors = []
page.on('console', (message) => {
  if (message.type() === 'error') consoleErrors.push(message.text())
})
page.on('pageerror', (error) => consoleErrors.push(String(error)))

async function text() {
  return page.evaluate(() => document.body.innerText)
}
async function waitForText(needle, timeout = 15000) {
  const started = Date.now()
  while (Date.now() - started < timeout) {
    if ((await text()).includes(needle)) return true
    await sleep(250)
  }
  return false
}
async function clickButton(label) {
  const handle = await page.evaluateHandle((wanted) =>
    [...document.querySelectorAll('button')].find((button) => button.textContent?.trim().startsWith(wanted)), label)
  const element = handle.asElement()
  if (!element) throw new Error(`no button "${label}"`)
  await element.click()
}
async function upload(groupNumber, file) {
  const input = await page.$(`section[aria-labelledby="jt-group-${groupNumber}"] input[type=file]`)
  await input.uploadFile(join(fixtures, file))
}
async function typeInto(groupNumber, labelText, value) {
  const handle = await page.evaluateHandle((n, wanted) => {
    const section = document.querySelector(`section[aria-labelledby="jt-group-${n}"]`)
    const label = [...section.querySelectorAll('label')].find((item) => item.textContent?.includes(wanted))
    return label?.querySelector('input')
  }, groupNumber, labelText)
  const element = handle.asElement()
  if (!element) throw new Error(`no field "${labelText}"`)
  await element.click()
  await page.keyboard.down('Control')
  await page.keyboard.press('KeyA')
  await page.keyboard.up('Control')
  await page.keyboard.press('Backspace')
  await element.type(value)
}
async function saveGroup(groupNumber) {
  await page.evaluate((n) => {
    const section = document.querySelector(`section[aria-labelledby="jt-group-${n}"]`)
    ;[...section.querySelectorAll('button')].find((button) => button.textContent?.trim() === 'Save').click()
  }, groupNumber)
  await sleep(700)
}

await page.setViewport({ width: 1440, height: 1000 })
await page.goto(base, { waitUntil: 'networkidle0' })
await page.evaluate(() => sessionStorage.clear())
await page.reload({ waitUntil: 'networkidle0' })

// ---- Screen 1
expect(await waitForText('Bring your own evidence'), 'intro screen renders')
await page.screenshot({ path: join(out, '01-new-trial.png'), fullPage: true })
const disabled = await page.evaluate(() => [...document.querySelectorAll('button')].find((b) => b.textContent.includes('Start a trial')).disabled)
expect(disabled, 'cannot start before acknowledging the privacy notice')
await page.evaluate(() => [...document.querySelectorAll('input[type=checkbox]')].at(-1).click())
await clickButton('Start a trial')
expect(await page.waitForSelector('section[aria-labelledby="jt-group-1"]', { timeout: 15000 }).then(() => true, () => false),
  'trial created, evidence screen shown')
const trialId = (await text()).match(/tr_[a-z2-7]{26}/)?.[0]
expect(Boolean(trialId), 'trial ID is shown')

// ---- Screen 2: partial evidence first (one photo), then the rest
await upload(2, 'IMG_before.jpg')
expect(await waitForText('Processed'), 'photo uploaded and processed (polling)')
await upload(2, 'notes.txt')
expect(await waitForText('is not accepted here'), 'unsupported file refused in the browser')

await page.select('section[aria-labelledby="jt-group-2"] select', 'after')
await upload(2, 'IMG_after.jpg')
await sleep(500)
await upload(3, 'slip.jpg')
await upload(4, 'trace.json')
await upload(1, 'bill.pdf')
await sleep(4000)

await typeInto(1, 'Claimed quantity', '9.3')
await typeInto(1, 'Rate', '1800')
await typeInto(1, 'Claimed amount', '16740')
await saveGroup(1)
await typeInto(5, 'Drain point', '22.5801, 88.4712')
await saveGroup(5)
await typeInto(6, 'Site centre', '22.5600, 88.4300')
await typeInto(6, 'Geofence radius', '200')
await saveGroup(6)
const group6 = await page.$eval('section[aria-labelledby="jt-group-6"]', (el) => el.innerText)
expect(/Supplied/.test(group6), `disposal site saved${/Supplied/.test(group6) ? '' : `: ${group6.replace(/\s+/g, ' ')}`}`)
await page.evaluate(() => document.querySelectorAll('details.jt-more').forEach((d) => { d.open = true }))
await typeInto(2, 'Work window start', '2026-10-01T00:00:00+05:30')
await typeInto(2, 'Work window end', '2026-10-10T00:00:00+05:30')
await saveGroup(2)
await typeInto(4, 'Truck registration', 'WB 00 MK 0001')
await typeInto(4, 'Rated capacity', '10')
await saveGroup(4)
expect(await waitForText('Supplied'), 'details saved and groups show readiness')
await sleep(1500)
await page.screenshot({ path: join(out, '02-evidence.png'), fullPage: true })
await page.evaluate(() => window.scrollTo(0, 0))
await sleep(600)
await page.screenshot({ path: join(out, '02b-evidence-map.png') })

// ---- Screens 3-4
const waiting = await waitForText('Run analysis', 20000)
expect(waiting, 'all files processed, analysis available')
await clickButton('Run analysis')
expect(await waitForText('What the evidence supports'), 'results screen renders')
const body = await text()
expect(body.includes('NOT EVALUATED — INSUFFICIENT EVIDENCE'), 'missing evidence shown as NOT EVALUATED')
expect(body.includes('CONSISTENT, NOT PROOF'), 'R1 against a supplied point is consistent, not pass')
expect(body.includes('Offline mock'), 'mock readings are labelled as mock')
expect(/ai observation|not a model result/i.test(body), 'AI output labelled apart from findings')
await page.evaluate(() => document.querySelectorAll('.jt-obs img').forEach((img) => { img.loading = 'eager'; img.scrollIntoView() }))
await sleep(1500)
const images = await page.$$eval('.jt-obs img', (list) => list.map((img) => img.naturalWidth))
expect(images.length >= 2 && images.every((width) => width > 0), 'original evidence previews load')
await page.screenshot({ path: join(out, '03-results.png'), fullPage: true })

// ---- reload keeps the trial
await page.reload({ waitUntil: 'networkidle0' })
expect(await waitForText(trialId ?? 'tr_'), 'trial survives a reload in this tab')

// ---- mobile width
await page.setViewport({ width: 390, height: 844 })
await sleep(800)
const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)
expect(!overflow, 'no horizontal scroll at 390 px')
await page.screenshot({ path: join(out, '04-mobile.png'), fullPage: true })
await page.setViewport({ width: 1440, height: 1000 })

// ---- Screen 5: start another, deleting this one
await clickButton('Start another trial')
await clickButton('Delete this trial now')
expect(await waitForText('Bring your own evidence'), 'reset returns to a new, empty trial')

const relevantErrors = consoleErrors.filter((line) => !/fonts\.googleapis|ERR_INTERNET_DISCONNECTED|Failed to load resource/.test(line))
expect(relevantErrors.length === 0, `no console errors${relevantErrors.length ? `: ${relevantErrors.join(' | ')}` : ''}`)

await browser.close()
console.log(failures.length ? `\n${failures.length} check(s) failed` : '\nall browser checks passed')
process.exit(failures.length ? 1 : 0)
