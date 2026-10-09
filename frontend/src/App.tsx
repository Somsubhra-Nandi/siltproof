import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import BillSheet from './components/BillSheet'
import type { Phase, SaveState } from './components/BillSheet'
import CaseFile from './components/CaseFile'
import MapView from './components/MapView'
import type { MapHandle, MapMode } from './components/MapView'
import { decide, getBill, getDrain, getEvidenceSummary, offline, runVerification } from './api'
import { grouped, rupees } from './format'
import { caseFacts, reasonFor } from './lib/caseFacts'
import { round, summarise } from './lib/ledger'
import type { Kind } from './lib/ledger'
import { ease, tween, useReducedMotion, wait } from './lib/motion'
import type { Bill, Drain, DrainRow, Photo } from './types'
import './App.css'

function message(cause: unknown) {
  return cause instanceof Error ? cause.message : String(cause)
}

const params = () =>
  typeof window === 'undefined' ? new URLSearchParams() : new URLSearchParams(window.location.search)

/**
 * ?drain=14 opens that drain on load (no animation). It also keeps the
 * screenshot scripts deterministic: clicking a ring on a WebGL canvas from a
 * script is not.
 */
function drainFromUrl(): string | null {
  return params().get('drain')
}

/** Mirror the open drain into the URL, so a reload lands in the same place. */
function syncUrl(drainId: string | null) {
  if (typeof window === 'undefined') return
  const query = new URLSearchParams(window.location.search)
  if (drainId) query.set('drain', drainId)
  else query.delete('drain')
  const text = query.toString()
  window.history.replaceState(null, '', `${window.location.pathname}${text ? `?${text}` : ''}`)
}

// Don't refetch a drain's links more often than this when images fail.
const REFRESH_COOLDOWN_MS = 20000
const SAVE_MIN_MS = 500
const MORPH_MS = 1000

type Rect = { left: number; top: number; width: number; height: number }

function App() {
  const reduced = useReducedMotion()

  const [bill, setBill] = useState<Bill | null>(null)
  const [billError, setBillError] = useState<string | null>(null)
  const [verifying, setVerifying] = useState(false)
  const [verifyLabel, setVerifyLabel] = useState('Starting verification')
  const [verifyProgress, setVerifyProgress] = useState(0)
  // Only set while the sweep runs: the drains whose verdicts may show so far.
  const [revealed, setRevealed] = useState<Set<string> | null>(null)
  const [slam, setSlam] = useState(false)
  const [enter, setEnter] = useState(false)

  const [details, setDetails] = useState<Record<string, Drain>>({})
  const [openId, setOpenId] = useState<string | null>(() => drainFromUrl())
  const [tripId, setTripId] = useState<string | null>(null)
  const [mode, setMode] = useState<MapMode>(() => (drainFromUrl() ? 'case' : 'overview'))
  const [settled, setSettled] = useState(false)
  const [originalFor, setOriginalFor] = useState<{ key: string; photo: Photo | null } | null>(null)
  const [expired, setExpired] = useState(false)
  const [hoverId, setHoverId] = useState<string | null>(null)

  const [saving, setSaving] = useState<SaveState | null>(null)
  const [saveError, setSaveError] = useState<{ drainId: string; message: string } | null>(null)
  const [decidedAt, setDecidedAt] = useState<Record<string, string>>({})
  // Drains whose decision was applied in this browser only (snapshot, or a
  // read-only bill): never described as saved.
  const [decidedLocally, setDecidedLocally] = useState<Record<string, boolean>>({})
  const [explaining, setExplaining] = useState(false)
  const [evidenceSummary, setEvidenceSummary] = useState<string | null>(null)
  const [announcement, setAnnouncement] = useState('')

  const mapRef = useRef<MapHandle>(null)
  const appRef = useRef<HTMLDivElement>(null)
  const frameRef = useRef<HTMLDivElement>(null)
  const slotRef = useRef<HTMLDivElement>(null)
  const requested = useRef(new Set<string>())
  const refreshedAt = useRef<Record<string, number>>({})
  const morphing = useRef(false)

  const verified = bill?.status === 'VERIFIED'
  const phase: Phase = !verified ? 'pending' : revealed ? 'sweeping' : 'verified'
  const rate = bill?.summary.ratePerTonne ?? 1800

  // ------------------------------------------------------------- loading
  useEffect(() => {
    getBill()
      .then(setBill)
      .catch((cause) => setBillError(message(cause)))
  }, [])

  const loadDrain = useCallback(async (drainId: string) => {
    const detail = await getDrain(drainId)
    setDetails((current) => ({ ...current, [drainId]: detail }))
    return detail
  }, [])

  // Once verified, fetch each flagged drain's record: its findings give the
  // one-line reasons on the sheet and the map.
  useEffect(() => {
    if (!bill || !verified) return
    for (const row of bill.drains) {
      if (row.verdict === 'GREEN' || requested.current.has(row.drainId)) continue
      requested.current.add(row.drainId)
      loadDrain(row.drainId).catch(() => requested.current.delete(row.drainId))
    }
  }, [bill, verified, loadDrain])

  // The open drain, fetched fresh every time it opens, so its evidence links
  // are new. Signed links live only in memory.
  const openKey = openId ? `${openId}:${verified ? 'v' : 'p'}` : null
  const [loaded, setLoaded] = useState<{ key: string; error: string | null } | null>(null)
  useEffect(() => {
    if (!openId || !openKey) return
    let cancelled = false
    getDrain(openId)
      .then((detail) => {
        if (cancelled) return
        setDetails((current) => ({ ...current, [openId]: detail }))
        setLoaded({ key: openKey, error: null })
      })
      .catch((cause) => !cancelled && setLoaded({ key: openKey, error: message(cause) }))
    return () => {
      cancelled = true
    }
  }, [openId, openKey])
  const loadingDrain = openKey !== null && loaded?.key !== openKey
  const drainError = loaded && loaded.key === openKey ? loaded.error : null

  const drain = openId ? (details[openId] ?? null) : null
  const facts = useMemo(() => (drain ? caseFacts(drain, tripId) : null), [drain, tripId])

  // The photo an R3 finding says was copied lives on another drain.
  const r3Original = facts?.r3
    ? { drainId: String(facts.r3.evidence.originalDrainId ?? ''), key: String(facts.r3.evidence.original ?? '') }
    : null
  const originalKey = r3Original?.drainId ? `${r3Original.drainId}|${r3Original.key}` : null
  useEffect(() => {
    if (!r3Original?.drainId || !originalKey) return
    let cancelled = false
    getDrain(r3Original.drainId)
      .then((other) => {
        if (!cancelled) {
          setOriginalFor({ key: originalKey, photo: other.photos.find((photo) => photo.s3Key === r3Original.key) ?? null })
        }
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
    // Refetched with the drain, so the original's link is as fresh as the copy's.
  }, [originalKey, drain]) // eslint-disable-line react-hooks/exhaustive-deps
  const original = originalFor && originalFor.key === originalKey ? originalFor.photo : null

  // Live evidence links expire; fetch fresh ones before they do.
  useEffect(() => {
    const seconds = drain?.evidenceUrlExpiresInSeconds
    if (!openId || !seconds) return
    const timer = window.setTimeout(() => {
      loadDrain(openId).catch(() => setExpired(true))
    }, seconds * 800)
    return () => window.clearTimeout(timer)
  }, [openId, drain, loadDrain])

  const onEvidenceError = useCallback(() => {
    if (!openId) return
    const last = refreshedAt.current[openId] ?? 0
    if (Date.now() - last < REFRESH_COOLDOWN_MS) {
      setExpired(true)
      return
    }
    refreshedAt.current[openId] = Date.now()
    loadDrain(openId)
      .then(() => setExpired(false))
      .catch(() => setExpired(true))
  }, [openId, loadDrain])

  // ------------------------------------------------------------- derived
  const reasons = useMemo(() => {
    const out: Record<string, string> = {}
    for (const [id, detail] of Object.entries(details)) {
      if (detail.verdict) out[id] = reasonFor(detail)
    }
    return out
  }, [details])

  const flaggedTonnes = useMemo(() => {
    const out: Record<string, number> = {}
    for (const row of bill?.drains ?? []) {
      const detail = details[row.drainId]
      out[row.drainId] = !row.decision
        ? round(row.reviewTonnes + row.heldTonnes)
        : detail
          ? round(detail.trips.filter((t) => t.verdict && t.verdict !== 'VERIFIED').reduce((sum, t) => sum + t.claimedTonnes, 0))
          : round(row.reviewTonnes + row.heldTonnes)
    }
    return out
  }, [bill, details])

  // Mid-sweep, the sheet only knows the drains the line has passed.
  const sheetRows: DrainRow[] = useMemo(() => {
    if (!bill) return []
    if (!revealed) return bill.drains
    return bill.drains.map((row) =>
      revealed.has(row.drainId)
        ? row
        : { ...row, verdict: null, verifiedTonnes: 0, reviewTonnes: 0, heldTonnes: 0, failedRules: [] },
    )
  }, [bill, revealed])

  const sheetSummary = useMemo(() => {
    if (!bill) return null
    if (!revealed) return bill.summary
    return summarise(sheetRows, rate, bill.summary.claimedTonnes)
  }, [bill, revealed, sheetRows, rate])

  // -------------------------------------------------------- verification
  const onVerify = async () => {
    if (verifying) return
    setVerifying(true)
    setBillError(null)
    setVerifyLabel('Starting verification')
    setVerifyProgress(0)
    try {
      const [next] = await Promise.all([runVerification(), wait(450, reduced)])
      const total = next.drains.reduce((sum, row) => sum + row.tripCount, 0)
      const trips = Object.fromEntries(next.drains.map((row) => [row.drainId, row.tripCount]))
      const seen = new Set<string>()
      setSlam(false)
      setRevealed(new Set())
      setBill(next)
      requested.current.clear()
      if (mapRef.current && !reduced) {
        await mapRef.current.sweep((ids, progress) => {
          ids.forEach((id) => seen.add(id))
          setRevealed(new Set(seen))
          setVerifyProgress(progress)
          const checked = [...seen].reduce((sum, id) => sum + (trips[id] ?? 0), 0)
          setVerifyLabel(`Checking trip ${Math.min(checked, total)} of ${total}`)
        })
      }
      setRevealed(null)
      setSlam(!reduced)
      setEnter(!reduced)
      const flagged = next.drains.filter((row) => row.verdict !== 'GREEN').length
      window.setTimeout(() => {
        setSlam(false)
        setEnter(false)
      }, 380 + flagged * 90 + 900)
      setAnnouncement(
        `Verification done. ${rupees(next.summary.heldRupees)} held, ${grouped(next.summary.verifiedTonnes)} t verified.`,
      )
    } catch (cause) {
      setBillError(message(cause))
      setRevealed(null)
    } finally {
      setVerifying(false)
    }
  }

  // ---------------------------------------------------------- navigation
  const openDrain = useCallback(
    async (drainId: string) => {
      if (mode === 'flying') return
      if (mode === 'case' && drainId === openId) return
      setHoverId(null)
      setTripId(null)
      setExpired(false)
      setSaveError(null)
      setEvidenceSummary(null)
      setOpenId(drainId)
      syncUrl(drainId)

      let detail: Drain | null = details[drainId] ?? null
      if (!detail && verified && !reduced && mapRef.current) {
        const fetched = await getDrain(drainId).catch(() => null)
        if (fetched) setDetails((current) => ({ ...current, [drainId]: fetched }))
        detail = fetched
      }
      const f = detail ? caseFacts(detail) : null
      // One flight, for a drain whose trace stops short of the dump site.
      if (detail && f && verified && !reduced && mapRef.current && f.trip && !f.reachedDump) {
        setMode('flying')
        await mapRef.current.fly(detail, f)
      }
      setSettled(false)
      setMode('case')
    },
    [mode, openId, details, verified, reduced],
  )

  const closeCase = useCallback(() => {
    setOpenId(null)
    setTripId(null)
    setSettled(false)
    setMode('overview')
    setSaveError(null)
    syncUrl(null)
  }, [])

  // Escape leaves the case file, unless a confirmation is open.
  useEffect(() => {
    if (mode !== 'case') return
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || document.querySelector('.confirm')) return
      closeCase()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [mode, closeCase])

  const inCase = mode === 'case' && openId !== null && bill !== null

  // ---------------------------------------------------------- the frame
  // The one map moves between the overview plate and Exhibit A's slot.
  const overviewRect = useCallback((): Rect => {
    const app = appRef.current
    if (!app) return { left: 0, top: 0, width: 0, height: 0 }
    const width = app.clientWidth
    const height = app.clientHeight
    if (width < 1100) return { left: 16, top: 16, width: width - 32, height: Math.round(height * 0.55) }
    const sheet = parseFloat(getComputedStyle(app).getPropertyValue('--sheet-w')) || 500
    return { left: sheet + 30, top: 30, width: width - sheet - 60, height: height - 60 }
  }, [])

  const slotRect = useCallback((): Rect | null => {
    const app = appRef.current
    const slot = slotRef.current
    if (!app || !slot) return null
    const a = app.getBoundingClientRect()
    const r = slot.getBoundingClientRect()
    return { left: r.left - a.left + app.scrollLeft, top: r.top - a.top + app.scrollTop, width: r.width, height: r.height }
  }, [])

  const place = useCallback((rect: Rect) => {
    const frame = frameRef.current
    if (!frame) return
    frame.style.left = `${rect.left}px`
    frame.style.top = `${rect.top}px`
    frame.style.width = `${rect.width}px`
    frame.style.height = `${rect.height}px`
  }, [])

  useLayoutEffect(() => {
    const frame = frameRef.current
    if (!frame) return
    if (mode !== 'case') {
      place(overviewRect())
      const onResize = () => place(overviewRect())
      window.addEventListener('resize', onResize)
      return () => window.removeEventListener('resize', onResize)
    }

    const target = inCase ? slotRect() : null
    if (!target) return
    let cancelled = false
    const follow = () => {
      if (morphing.current) return
      const rect = slotRect()
      if (rect) place(rect)
    }
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(follow) : null
    if (slotRef.current) observer?.observe(slotRef.current)
    window.addEventListener('resize', follow)

    if (!settled) {
      const from = {
        left: frame.offsetLeft,
        top: frame.offsetTop,
        width: frame.offsetWidth,
        height: frame.offsetHeight,
      }
      morphing.current = true
      tween(
        MORPH_MS,
        (p) => {
          if (cancelled) return
          const to = slotRect() ?? target
          place({
            left: from.left + (to.left - from.left) * p,
            top: from.top + (to.top - from.top) * p,
            width: from.width + (to.width - from.width) * p,
            height: from.height + (to.height - from.height) * p,
          })
        },
        ease.inOut,
        reduced || from.width === 0,
      ).then(() => {
        morphing.current = false
        if (!cancelled) {
          follow()
          setSettled(true)
        }
      })
    } else {
      place(target)
    }

    return () => {
      cancelled = true
      morphing.current = false
      observer?.disconnect()
      window.removeEventListener('resize', follow)
    }
  }, [mode, settled, openId, inCase, place, overviewRect, slotRect, reduced])

  // ------------------------------------------------------------ decisions
  const onDecide = useCallback(
    async (drainId: string, kind: Kind, note: string) => {
      setSaving({ drainId, kind })
      setSaveError(null)
      try {
        if (offline && params().get('simulate') === 'save-error') {
          await wait(SAVE_MIN_MS, reduced)
          throw new Error('The bill service did not answer.')
        }
        const [result] = await Promise.all([decide(drainId, kind, note), wait(SAVE_MIN_MS, reduced)])
        const now = new Date().toISOString()
        setBill((current) => (current ? { ...current, summary: result.summary, drains: result.drains } : current))
        setDetails((current) =>
          current[drainId]
            ? { ...current, [drainId]: { ...current[drainId], decision: result.decision, note: result.note, decidedAt: now } }
            : current,
        )
        setDecidedAt((current) => ({ ...current, [drainId]: now }))
        setDecidedLocally((current) => ({ ...current, [drainId]: Boolean(result.local) }))
        const row = result.drains.find((entry) => entry.drainId === drainId)
        const lead = result.local
          ? 'Decision recorded in this browser only, not saved to AWS.'
          : 'Decision saved.'
        setAnnouncement(
          kind === 'APPROVE'
            ? `${lead} Drain ${drainId} approved; ${grouped(row?.verifiedTonnes ?? 0)} t payable.`
            : `${lead} Drain ${drainId} held; ${rupees((row?.heldTonnes ?? 0) * rate)} stays with the ward.`,
        )
        return true
      } catch (cause) {
        setSaveError({ drainId, message: message(cause) })
        return false
      } finally {
        setSaving(null)
      }
    },
    [rate, reduced],
  )

  useEffect(() => {
    if (!announcement) return
    const timer = window.setTimeout(() => setAnnouncement(''), 3200)
    return () => window.clearTimeout(timer)
  }, [announcement])

  const onExplain = async () => {
    if (!openId) return
    setExplaining(true)
    try {
      const result = await getEvidenceSummary(openId)
      setEvidenceSummary(result.summary)
    } catch (cause) {
      setEvidenceSummary(`Could not write a summary: ${message(cause)}`)
    } finally {
      setExplaining(false)
    }
  }

  const row = openId ? (bill?.drains.find((entry) => entry.drainId === openId) ?? null) : null

  return (
    <div ref={appRef} className={`app mode-${mode} ${settled ? 'settled' : ''}`}>
      <div className="sheet-wrap" inert={mode !== 'overview' ? true : undefined}>
        <BillSheet
          bill={bill}
          error={billError}
          phase={phase}
          rows={sheetRows}
          summary={sheetSummary}
          revealed={revealed}
          verifying={verifying}
          verifyLabel={verifyLabel}
          verifyProgress={verifyProgress}
          slam={slam}
          enter={enter}
          reasons={reasons}
          flaggedTonnes={flaggedTonnes}
          hoverId={hoverId}
          saving={saving}
          saveError={saveError}
          onHover={setHoverId}
          onVerify={onVerify}
          onOpen={openDrain}
          onDecide={onDecide}
          onClearError={() => setSaveError(null)}
        />
      </div>

      {inCase && (
        <CaseFile
          bill={bill}
          drain={drain}
          row={row}
          facts={facts}
          loading={loadingDrain}
          error={drainError}
          verified={verified}
          settled={settled}
          slotRef={slotRef}
          original={original}
          forcePhotoSlot={params().get('photos') === 'slot'}
          evidenceExpired={expired}
          flaggedTonnes={openId ? (flaggedTonnes[openId] ?? 0) : 0}
          decidedAt={(openId && (decidedAt[openId] ?? drain?.decidedAt)) || null}
          decidedLocally={Boolean(openId && decidedLocally[openId])}
          saving={saving && saving.drainId === openId ? saving.kind : null}
          saveError={saveError && saveError.drainId === openId ? saveError.message : null}
          explaining={explaining}
          evidenceSummary={evidenceSummary}
          onBack={closeCase}
          onSelectTrip={setTripId}
          onEvidenceError={onEvidenceError}
          onExplain={onExplain}
          onDecide={(kind, note) => onDecide(openId!, kind, note)}
          onClearError={() => setSaveError(null)}
        />
      )}

      <div ref={frameRef} className="mapframe">
        <MapView
          ref={mapRef}
          rows={bill?.drains ?? []}
          revealed={revealed}
          verified={verified}
          hoverId={hoverId}
          mode={mode}
          caseDrain={mode === 'overview' ? null : drain}
          facts={mode === 'overview' ? null : facts}
          reasons={reasons}
          rate={rate}
          reducedMotion={reduced}
          onHover={setHoverId}
          onSelect={openDrain}
        />
      </div>

      <div className={`toast ${announcement ? 'on' : ''}`} role="status" aria-live="polite">
        {announcement}
      </div>
    </div>
  )
}

export default App
