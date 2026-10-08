import { useCallback, useEffect, useState } from 'react'

import DrainPanel from './components/DrainPanel'
import MapView from './components/MapView'
import SummaryBar from './components/SummaryBar'
import { decide, getBill, getDrain, getEvidenceSummary, runVerification } from './api'
import type { Bill, Drain } from './types'
import './App.css'

function message(cause: unknown) {
  return cause instanceof Error ? cause.message : String(cause)
}

/**
 * ?drain=14 opens that drain on load. Handy when working on the panel, and it
 * is what makes the screenshot script deterministic - clicking a polygon on a
 * WebGL canvas from a script is not.
 */
function drainFromUrl(): string | null {
  if (typeof window === 'undefined') return null
  return new URLSearchParams(window.location.search).get('drain')
}

function App() {
  const [bill, setBill] = useState<Bill | null>(null)
  const [billError, setBillError] = useState<string | null>(null)
  const [verifying, setVerifying] = useState(false)
  // True once a verification has happened, which is when the numbers are
  // worth animating and the drains worth colouring in.
  const [animate, setAnimate] = useState(false)

  const [selectedDrainId, setSelectedDrainId] = useState<string | null>(() => drainFromUrl())
  const [drain, setDrain] = useState<Drain | null>(null)
  const [drainError, setDrainError] = useState<string | null>(null)
  const [selectedTripId, setSelectedTripId] = useState<string | null>(null)
  const [reloadToken, setReloadToken] = useState(0)

  const [deciding, setDeciding] = useState(false)
  const [explaining, setExplaining] = useState(false)
  const [evidenceSummary, setEvidenceSummary] = useState<string | null>(null)

  // The panel is loading whenever the open drain is not the one on screen.
  const drainLoading =
    selectedDrainId !== null && drain?.drainId !== selectedDrainId && drainError === null

  useEffect(() => {
    getBill()
      .then(setBill)
      .catch((cause) => setBillError(message(cause)))
  }, [])

  useEffect(() => {
    if (!selectedDrainId) return
    let cancelled = false

    getDrain(selectedDrainId)
      .then((detail) => {
        if (cancelled) return
        setDrain(detail)
        // Open on the first flagged trip: that is the one worth looking at,
        // and its route is the one the map should draw.
        const flagged = detail.trips.find((trip) => trip.verdict !== 'VERIFIED')
        setSelectedTripId((flagged ?? detail.trips[0])?.tripId ?? null)
      })
      .catch((cause) => {
        if (!cancelled) setDrainError(message(cause))
      })

    return () => {
      cancelled = true
    }
  }, [selectedDrainId, reloadToken])

  const openDrain = useCallback((drainId: string) => {
    setSelectedDrainId(drainId)
    setDrainError(null)
    setEvidenceSummary(null)
  }, [])

  const onVerify = async () => {
    setVerifying(true)
    setBillError(null)
    try {
      setBill(await runVerification())
      setAnimate(true)
      // Pull the open drain again: it now has verdicts.
      setReloadToken((token) => token + 1)
    } catch (cause) {
      setBillError(message(cause))
    } finally {
      setVerifying(false)
    }
  }

  const onDecide = async (decision: 'APPROVE' | 'HOLD', note: string) => {
    if (!selectedDrainId) return
    setDeciding(true)
    try {
      const result = await decide(selectedDrainId, decision, note)
      setBill((current) =>
        current ? { ...current, summary: result.summary, drains: result.drains } : current,
      )
      setDrain((current) =>
        current ? { ...current, decision: result.decision, note: result.note } : current,
      )
    } catch (cause) {
      setDrainError(message(cause))
    } finally {
      setDeciding(false)
    }
  }

  const onExplain = async () => {
    if (!selectedDrainId) return
    setExplaining(true)
    try {
      const result = await getEvidenceSummary(selectedDrainId)
      setEvidenceSummary(result.summary)
    } catch (cause) {
      setEvidenceSummary(`Could not generate a summary: ${message(cause)}`)
    } finally {
      setExplaining(false)
    }
  }

  const closePanel = () => {
    setSelectedDrainId(null)
    setDrain(null)
    setSelectedTripId(null)
    setDrainError(null)
  }

  return (
    <div className="app">
      <SummaryBar
        bill={bill}
        verifying={verifying}
        animate={animate}
        onVerify={onVerify}
      />

      {billError && <div className="banner">{billError}</div>}

      {bill?.missingEvidence && Object.values(bill.missingEvidence).some(Boolean) && (
        <div className="banner banner-warn">
          Evidence gaps:{' '}
          {Object.entries(bill.missingEvidence)
            .filter(([, count]) => count)
            .map(([name, count]) => `${count} ${name.replace(/([A-Z])/g, ' $1').toLowerCase()}`)
            .join(', ')}
          . Those trips are in review, not held.
        </div>
      )}

      <div className="workspace">
        <MapView
          drains={bill?.drains ?? []}
          selectedDrainId={selectedDrainId}
          detail={drain}
          selectedTripId={selectedTripId}
          animate={animate}
          onSelect={openDrain}
        />
        {selectedDrainId && (
          <DrainPanel
            drain={drainLoading ? null : drain}
            loading={drainLoading}
            error={drainError}
            selectedTripId={selectedTripId}
            onSelectTrip={setSelectedTripId}
            onDecide={onDecide}
            onExplain={onExplain}
            explaining={explaining}
            evidenceSummary={evidenceSummary}
            deciding={deciding}
            onClose={closePanel}
          />
        )}
      </div>
    </div>
  )
}

export default App
