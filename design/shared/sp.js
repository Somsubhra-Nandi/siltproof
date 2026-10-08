/*
 * Shared by the three design prototypes. Loads the real API snapshot
 * (copied from frontend/public/data) and derives everything spatial from it,
 * so no prototype hard-codes a drain position: swap the data for a new
 * ward and every layout, label and camera follows.
 */
const SP = (() => {
  const BASE = new URL('./data/', document.currentScript.src).href
  const EVIDENCE = new URL('./evidence/', document.currentScript.src).href

  const params = new URLSearchParams(location.search)
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches

  async function json(name) {
    const res = await fetch(BASE + name)
    if (!res.ok) throw new Error(`${name}: ${res.status}`)
    return res.json()
  }

  async function load() {
    const [bill, drain14, drains, dumpsite, basemap, findings] = await Promise.all([
      json('bill.json'),
      json('drain-14.json'),
      json('drains.geojson'),
      json('dumpsite.geojson'),
      json('basemap.geojson'),
      json('findings-by-drain.json'),
    ])
    const byId = Object.fromEntries(bill.drains.map((d) => [d.drainId, d]))
    for (const f of drains.features) {
      const row = byId[f.properties.drainId]
      f.properties.centroid = centroid(f.geometry)
      f.properties.verdict = row?.verdict ?? null
      f.properties.claimed = row?.claimedTonnes ?? 0
      f.properties.verified = row?.verifiedTonnes ?? 0
      f.properties.review = row?.reviewTonnes ?? 0
      f.properties.held = row?.heldTonnes ?? 0
    }
    return { bill, findings, pending: pendingBill(bill), drain14, drains, dumpsite, basemap, facts: caseFacts(drain14, dumpsite) }
  }

  /** The bill as it reads before verification: claim only, no verdicts. */
  function pendingBill(bill) {
    const s = bill.summary
    return {
      ...bill,
      status: 'PENDING',
      summary: { ...s, verifiedTonnes: 0, reviewTonnes: 0, heldTonnes: 0, verifiedRupees: 0, reviewRupees: 0, heldRupees: 0, red: 0, amber: 0, green: 0 },
      drains: bill.drains.map((d) => ({ ...d, verdict: null, verifiedTonnes: 0, reviewTonnes: 0, heldTonnes: 0, failedRules: [] })),
    }
  }

  // ---------- geometry
  function ring(geom) {
    return geom.type === 'Polygon' ? geom.coordinates[0] : geom.coordinates[0][0]
  }
  function centroid(geom) {
    const r = ring(geom)
    let x = 0, y = 0
    for (const [a, b] of r) { x += a; y += b }
    return [x / r.length, y / r.length]
  }
  function bounds(points) {
    let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity
    for (const [x, y] of points) { w = Math.min(w, x); s = Math.min(s, y); e = Math.max(e, x); n = Math.max(n, y) }
    return [[w, s], [e, n]]
  }
  function drainBounds(drains) {
    return bounds(drains.features.flatMap((f) => ring(f.geometry)))
  }
  function metres([x1, y1], [x2, y2]) {
    const R = 6371000, toR = Math.PI / 180
    const dLat = (y2 - y1) * toR, dLon = (x2 - x1) * toR
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(y1 * toR) * Math.cos(y2 * toR) * Math.sin(dLon / 2) ** 2
    return 2 * R * Math.asin(Math.sqrt(a))
  }
  function lineLength(coords) {
    let m = 0
    for (let i = 1; i < coords.length; i++) m += metres(coords[i - 1], coords[i])
    return m
  }
  /** The first `frac` of a line, by distance, ending on an interpolated point. */
  function slice(coords, frac) {
    if (frac >= 1) return coords
    const total = lineLength(coords) * Math.max(0, frac)
    const out = [coords[0]]
    let run = 0
    for (let i = 1; i < coords.length; i++) {
      const seg = metres(coords[i - 1], coords[i])
      if (run + seg >= total) {
        const t = seg ? (total - run) / seg : 0
        out.push([coords[i - 1][0] + (coords[i][0] - coords[i - 1][0]) * t, coords[i - 1][1] + (coords[i][1] - coords[i - 1][1]) * t])
        return out
      }
      run += seg
      out.push(coords[i])
    }
    return out
  }
  /** A small square around a point, `size` metres on a side: a data-bar footprint. */
  function square([x, y], size) {
    const dy = size / 2 / 111320, dx = size / 2 / (111320 * Math.cos((y * Math.PI) / 180))
    return { type: 'Polygon', coordinates: [[[x - dx, y - dy], [x + dx, y - dy], [x + dx, y + dy], [x - dx, y + dy], [x - dx, y - dy]]] }
  }
  /** A ring around a point, for scan sweeps. */
  function circle([x, y], radiusM, steps = 96) {
    const pts = []
    for (let i = 0; i <= steps; i++) {
      const a = (i / steps) * Math.PI * 2
      pts.push([x + (radiusM * Math.cos(a)) / (111320 * Math.cos((y * Math.PI) / 180)), y + (radiusM * Math.sin(a)) / 111320])
    }
    return { type: 'Polygon', coordinates: [pts] }
  }

  /** What the drain 14 case says, pulled from the fixture rather than typed in. */
  function caseFacts(drain, dumpsite) {
    const trips = drain.trips
    const one = trips.find((t) => t.verdict === 'HOLD') ?? trips[0]
    const ends = trips.map((t) => t.actualRoute[t.actualRoute.length - 1])
    const stop = [ends.reduce((a, p) => a + p[0], 0) / ends.length, ends.reduce((a, p) => a + p[1], 0) / ends.length]
    const dump = dumpsite.features[0].properties.center
    const r8 = one.findings.find((f) => f.rule === 'R8')
    const r3 = drain.findings.find((f) => f.rule === 'R3' && f.evidence.hammingDistance === 0) ?? drain.findings[0]
    const hhmm = (iso) => (iso ? iso.slice(11, 16) : '—')
    return {
      trip: one,
      stop,
      dump,
      shortOfDumpM: metres(stop, dump),
      claimedRoute: drain.claimedRoute,
      claimedKm: lineLength(drain.claimedRoute) / 1000,
      drivenKm: one.actualRouteDistanceM / 1000,
      slipTimeIn: one.slip.timeIn,
      slipTimeOut: one.slip.timeOut,
      leftDrain: hhmm(one.startTime),
      stopped: hhmm(one.arrivalTime),
      traceEnds: r8 ? hhmm(r8.evidence.arrival) : hhmm(one.arrivalTime),
      minutesEarly: r8 ? Math.trunc(r8.evidence.minutesEarly) : null,  // the finding's own message truncates
      reuse: r3,
      wardCentre: null,
      tripsHeld: trips.filter((t) => t.verdict === 'HOLD').length,
    }
  }

  // ---------- formatting (Indian grouping, lakh for money)
  const nf = new Intl.NumberFormat('en-IN')
  const rupees = (r) => '₹' + nf.format(Math.round(r))
  const lakh = (r, digits = 2) => (r / 100000).toFixed(digits)
  const tonnes = (t, digits = 0) => nf.format(Number(t.toFixed(digits))) + ' t'
  const num = (n, digits = 0) => new Intl.NumberFormat('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n)

  // ---------- motion
  const ease = {
    out: (t) => 1 - Math.pow(1 - t, 3),
    inOut: (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2),
    linear: (t) => t,
  }
  function tween(ms, onFrame, curve = ease.out) {
    return new Promise((resolve) => {
      if (reducedMotion || ms <= 0) { onFrame(1); return resolve() }
      const t0 = performance.now()
      const step = (now) => {
        const t = Math.min(1, (now - t0) / ms)
        onFrame(curve(t))
        if (t < 1) requestAnimationFrame(step)
        else resolve()
      }
      requestAnimationFrame(step)
    })
  }
  const wait = (ms) => new Promise((r) => setTimeout(r, reducedMotion ? 0 : ms))

  /** Resolve once the map has drawn its first full frame. */
  function mapReady(map) {
    return new Promise((resolve) => {
      if (map.loaded() && map.areTilesLoaded()) return resolve()
      map.once('idle', resolve)
    })
  }

  /** One plain sentence for why a drain is flagged, taken from its first finding. */
  const RULE_ORDER = ['R3', 'R5', 'R8', 'R4', 'R7', 'R6', 'R9', 'R2', 'R1', 'R10', 'GPS_GAP']
  function reason(findings, drainId) {
    const f = findings[drainId]
    if (!f) return ''
    const rule = RULE_ORDER.find((r) => f.firstMessage[r])
    return rule ? f.firstMessage[rule] : ''
  }

  function evidence(name) { return EVIDENCE + name }

  /** Basemap filtered to roads and water, for each direction to style. */
  function basemapLayers(basemap, c) {
    return {
      sources: { basemap: { type: 'geojson', data: basemap } },
      layers: [
        { id: 'bg', type: 'background', paint: { 'background-color': c.ground } },
        { id: 'water', type: 'fill', source: 'basemap', filter: ['all', ['==', ['get', 'kind'], 'water'], ['==', ['geometry-type'], 'Polygon']], paint: { 'fill-color': c.water } },
        { id: 'water-line', type: 'line', source: 'basemap', filter: ['all', ['==', ['get', 'kind'], 'water'], ['==', ['geometry-type'], 'LineString']], paint: { 'line-color': c.water, 'line-width': 2 } },
        { id: 'road-minor', type: 'line', source: 'basemap', filter: ['==', ['get', 'class'], 'minor'], paint: { 'line-color': c.minor, 'line-width': ['interpolate', ['linear'], ['zoom'], 12, 0.4, 16, 2.4] } },
        { id: 'road-major', type: 'line', source: 'basemap', filter: ['==', ['get', 'class'], 'major'], layout: { 'line-join': 'round', 'line-cap': 'round' }, paint: { 'line-color': c.major, 'line-width': ['interpolate', ['linear'], ['zoom'], 12, 1, 16, 5] } },
      ],
    }
  }

  return { params, reducedMotion, load, pendingBill, centroid, bounds, drainBounds, metres, lineLength, slice, square, circle, rupees, lakh, tonnes, num, ease, tween, wait, mapReady, evidence, reason, RULE_ORDER, basemapLayers }
})()
