import type { Ref } from 'react'

import CaseTimeline from './CaseTimeline'
import DecisionBar from './DecisionBar'
import ExhibitPhotos from './ExhibitPhotos'
import ExhibitSlip from './ExhibitSlip'
import Stamp from './Stamp'
import { grouped, lakh, rupees } from '../format'
import { routeTitle } from '../lib/caseFacts'
import type { CaseFacts } from '../lib/caseFacts'
import type { Kind } from '../lib/ledger'
import { buildTimeline } from '../lib/timeline'
import type { Bill, Drain, DrainRow, Photo } from '../types'

const WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten']

interface Props {
  bill: Bill
  drain: Drain | null
  row: DrainRow | null
  facts: CaseFacts | null
  loading: boolean
  error: string | null
  verified: boolean
  settled: boolean
  slotRef: Ref<HTMLDivElement>
  original: Photo | null
  forcePhotoSlot: boolean
  evidenceExpired: boolean
  flaggedTonnes: number
  decidedAt: string | null
  saving: Kind | null
  saveError: string | null
  explaining: boolean
  evidenceSummary: string | null
  onBack: () => void
  onSelectTrip: (tripId: string) => void
  onEvidenceError: () => void
  onExplain: () => void
  onDecide: (kind: Kind, note: string) => Promise<boolean>
  onClearError: () => void
}

function verdictLine(drain: Drain, facts: CaseFacts) {
  const n = drain.trips.length
  if (!drain.verdict) return 'Not checked yet.'
  if (drain.verdict === 'GREEN') return 'Every check passed.'
  const held = drain.trips.filter((trip) => trip.verdict === 'HOLD')
  if (held.length === n && n > 0) {
    const counts = new Set(held.map((trip) => trip.hardFails.length))
    if (counts.size === 1) {
      const k = held[0].hardFails.length
      return `Every trip failed ${WORDS[k] ?? k} hard ${k === 1 ? 'check' : 'checks'}.`
    }
    return 'Every trip failed a hard check.'
  }
  const parts = []
  if (facts.holdTrips) parts.push(`${facts.holdTrips} of ${n} trips held`)
  if (facts.reviewTrips) parts.push(`${facts.reviewTrips} in review`)
  return parts.length ? `${parts.join(', ')}.` : 'Flagged at drain level.'
}

function CaseFile({ slotRef, ...props }: Props) {
  const { bill, drain, row, facts } = props
  const rate = bill.summary.ratePerTonne
  const s = bill.summary
  const ready = drain && row && facts
  const timeline = ready ? buildTimeline(drain, facts) : null
  const routeFindings = ready
    ? facts.tripFindings.filter((f) => ['R5', 'R6', 'GPS_GAP', 'TRACE_MISSING'].includes(f.rule))
    : []
  // The rules this exhibit argues: the route checks, and R8, whose times it draws.
  const routeChips = ready
    ? facts.tripFindings.filter(
        (f, i, all) =>
          ['R5', 'R6', 'R8', 'GPS_GAP', 'TRACE_MISSING'].includes(f.rule) &&
          all.findIndex((other) => other.rule === f.rule) === i,
      )
    : []

  // ------------------------------------------------------------- money
  let money = null
  if (row) {
    const approved = row.decision === 'APPROVE'
    const amount = approved ? props.flaggedTonnes * rate : row.heldTonnes ? row.heldTonnes * rate : row.reviewTonnes * rate
    const caption = !props.verified
      ? 'not checked yet'
      : approved
        ? 'released on your approval'
        : row.heldTonnes
          ? `held on this drain, ${grouped(row.heldTonnes)} t`
          : row.reviewTonnes
            ? `for your review, ${grouped(row.reviewTonnes)} t`
            : 'payable as billed'
    const amountClass = approved || !row.heldTonnes ? (row.reviewTonnes ? 'review' : 'calm') : 'held'
    money = (
      <div className="case-money">
        <div className={`amt ${props.verified ? amountClass : 'calm'}`}>
          {props.verified ? rupees(amount || row.verifiedTonnes * rate) : rupees(row.claimedTonnes * rate)}
          <small>{props.verified ? caption : `claimed, ${grouped(row.claimedTonnes)} t, not checked yet`}</small>
        </div>
        {props.verified &&
          (approved ? (
            <Stamp kind="approved" big amount={rupees(amount)} />
          ) : row.heldTonnes ? (
            <Stamp kind="held" big amount={rupees(row.heldTonnes * rate)} />
          ) : row.reviewTonnes ? (
            <Stamp kind="review" big amount={rupees(row.reviewTonnes * rate)} />
          ) : null)}
        <div className="billbox">
          Whole bill now
          <br />
          Held <b className="mono">₹{lakh(s.heldRupees)} L</b>
          <br />
          Payable <b className="mono">₹{lakh(s.verifiedRupees)} L</b>
          <br />
          Review <b className="mono">₹{lakh(s.reviewRupees)} L</b>
        </div>
      </div>
    )
  }

  return (
    <section className={`casefile ${props.settled ? 'settled' : ''}`} aria-label="Drain investigation">
      <header className="case-head fade">
        <div>
          <button type="button" className="link back" onClick={props.onBack}>
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
              <path d="M10 3 5 8l5 5" fill="none" stroke="currentColor" strokeWidth="2" />
            </svg>
            All {bill.drains.length} drains
          </button>
          <h1>Drain {drain?.drainId ?? row?.drainId ?? ''}</h1>
          {drain && facts ? (
            <div className="dims">
              {drain.name}, <span className="num">{drain.lengthM.toFixed(0)} m</span> long,{' '}
              <span className="num">{drain.widthM} m</span> wide, <span className="num">{drain.depthM} m</span> deep.{' '}
              {drain.trips.length} trips, <span className="num">{grouped(drain.claimedTonnes)} t</span> claimed.{' '}
              {verdictLine(drain, facts)}
              <span className="sim-badge inline-badge">Simulated data</span>
            </div>
          ) : (
            <div className="dims">
              <div className="skeleton" style={{ width: 420 }} />
            </div>
          )}
        </div>
        {money}
      </header>

      {props.error && (
        <div className="err" role="alert">
          <b>Could not load the drain.</b> {props.error}
        </div>
      )}

      <div className="case-body">
        <article className="exhibit exA fade" aria-labelledby="exhibit-a">
          <div className="ex-head">
            <span className="ex-tag">Exhibit A</span>
            <h2 id="exhibit-a">{ready ? routeTitle(drain, facts) : 'The haul to the dump site'}</h2>
            <span className="rules">
              {drain && drain.trips.length > 1 && facts?.trip && (
                <label className="trip-pick">
                  <span>Trip</span>
                  <select value={facts.trip.tripId} onChange={(event) => props.onSelectTrip(event.target.value)}>
                    {drain.trips.map((trip) => (
                      <option key={trip.tripId} value={trip.tripId}>
                        {trip.tripNo}
                        {trip.verdict && trip.verdict !== 'VERIFIED' ? ` (${trip.verdict.toLowerCase()})` : ''}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {routeChips.map((f) => (
                <span key={f.rule} className={`rid ${f.severity === 'soft' ? 'soft' : ''}`}>
                  {f.rule}
                </span>
              ))}
            </span>
          </div>
          <div className="exA-slot" ref={slotRef} />
          {props.loading && !ready ? (
            <div className="timeline-loading">
              <div className="skeleton" style={{ width: '70%' }} />
              <div className="skeleton" style={{ width: '50%' }} />
            </div>
          ) : timeline ? (
            <CaseTimeline key={facts?.trip?.tripId} timeline={timeline} />
          ) : (
            <p className="quiet-note">No GPS times for this trip.</p>
          )}
          {routeFindings
            .filter((f) => f.rule !== 'R5')
            .slice(0, 1)
            .map((f) => (
              <div key={f.rule} className={`r8note ${f.severity}`}>
                <span className="k">
                  {f.rule}, {f.severity} check failed
                </span>
                {f.message}
              </div>
            ))}
        </article>

        <div className="right-col">
          <div className="fade" style={{ animationDelay: '120ms' }}>
            {ready ? (
              <ExhibitSlip
                trip={facts.trip}
                facts={facts}
                imageExpired={props.evidenceExpired}
                onImageError={props.onEvidenceError}
              />
            ) : (
              <article className="exhibit">
                <div className="skeleton" style={{ width: '60%' }} />
              </article>
            )}
          </div>
          <div className="fade" style={{ animationDelay: '240ms' }}>
            {ready ? (
              <ExhibitPhotos
                drain={drain}
                facts={facts}
                original={props.original}
                forceSlot={props.forcePhotoSlot}
                expired={props.evidenceExpired}
                onImageError={props.onEvidenceError}
              />
            ) : (
              <article className="exhibit">
                <div className="skeleton" style={{ width: '60%' }} />
              </article>
            )}
          </div>
        </div>
      </div>

      <div className="fade" style={{ animationDelay: '360ms' }}>
        {ready ? (
          <DecisionBar
            key={drain.drainId}
            bill={bill}
            drain={drain}
            row={row}
            facts={facts}
            verified={props.verified}
            flaggedTonnes={props.flaggedTonnes}
            decidedAt={props.decidedAt}
            saving={props.saving}
            saveError={props.saveError}
            explaining={props.explaining}
            evidenceSummary={props.evidenceSummary}
            onExplain={props.onExplain}
            onDecide={props.onDecide}
            onClearError={props.onClearError}
          />
        ) : (
          <section className="decide" aria-label="Your decision">
            <div className="skeleton" style={{ width: '40%' }} />
          </section>
        )}
      </div>
    </section>
  )
}

export default CaseFile
