import { useState } from 'react'

import ConfirmSheet from './ConfirmSheet'
import Stamp from './Stamp'
import { offline } from '../api'
import { grouped, lakh, rupees, windowText } from '../format'
import { previewDecision } from '../lib/ledger'
import type { Kind } from '../lib/ledger'
import type { Bill, DrainRow, MissingEvidence, Summary } from '../types'

export type Phase = 'pending' | 'sweeping' | 'verified'

export interface SaveState {
  drainId: string
  kind: Kind
}

interface Props {
  bill: Bill | null
  error: string | null
  phase: Phase
  /** The bill's rows; during the sweep only revealed rows carry verdicts. */
  rows: DrainRow[]
  /** The money so far: everything once verified, the revealed drains mid-sweep. */
  summary: Summary | null
  revealed: Set<string> | null
  verifying: boolean
  verifyLabel: string
  verifyProgress: number
  slam: boolean
  enter: boolean
  reasons: Record<string, string>
  /** Tonnes the evidence flagged on each drain, what an approval releases. */
  flaggedTonnes: Record<string, number>
  hoverId: string | null
  saving: SaveState | null
  saveError: { drainId: string; message: string } | null
  onHover: (drainId: string | null) => void
  onVerify: () => void
  onOpen: (drainId: string) => void
  onDecide: (drainId: string, kind: Kind, note: string) => Promise<boolean>
  onClearError: () => void
}

const pct = (part: number, whole: number) => `${whole ? (100 * part) / whole : 0}%`

function evidenceGaps(missing: MissingEvidence | null | undefined) {
  if (!missing) return null
  const parts = Object.entries(missing)
    .filter(([, count]) => count)
    .map(([name, count]) => `${count} ${name.replace(/([A-Z])/g, ' $1').toLowerCase()}`)
  return parts.length ? parts.join(', ') : null
}

function Ledger({ summary, pending }: { summary: Summary; pending: boolean }) {
  const s = summary
  return (
    <div className="ledger">
      <div className="claimrow">
        <span>Claimed</span>
        <b className="mono">
          {grouped(s.claimedTonnes)} t, ₹{lakh(s.claimedRupees)} L
        </b>
      </div>
      {pending ? (
        <>
          <div className="bar" aria-hidden="true" />
          <div className="keys">
            {['verified', 'for review', 'held'].map((what) => (
              <div key={what} className="na">
                <b>Not yet calculated</b>
                <span>{what}</span>
              </div>
            ))}
          </div>
        </>
      ) : (
        <>
          <div
            className="bar"
            role="img"
            aria-label={`${grouped(s.verifiedTonnes)} t verified, ${grouped(s.reviewTonnes)} t in review, ${grouped(s.heldTonnes)} t held, of ${grouped(s.claimedTonnes)} t claimed`}
          >
            <span className="v" style={{ width: pct(s.verifiedTonnes, s.claimedTonnes) }} />
            <span className="r" style={{ width: pct(s.reviewTonnes, s.claimedTonnes) }} />
            <span className="h" style={{ width: pct(s.heldTonnes, s.claimedTonnes) }} />
          </div>
          <div className="keys">
            <div className="v">
              <b className="mono">{grouped(s.verifiedTonnes)} t</b>
              <span>verified, ₹{lakh(s.verifiedRupees)} L payable</span>
            </div>
            <div className="r">
              <b className="mono">{grouped(s.reviewTonnes)} t</b>
              <span>your review, ₹{lakh(s.reviewRupees)} L</span>
            </div>
            <div className="h">
              <b className="mono">{grouped(s.heldTonnes)} t</b>
              <span>held, ₹{lakh(s.heldRupees)} L</span>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function BillSheet(props: Props) {
  const { bill, error, phase, rows, summary, revealed, reasons, hoverId } = props
  const [openReview, setOpenReview] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<{ drainId: string; kind: Kind } | null>(null)

  const trips = bill?.drains.reduce((sum, row) => sum + row.tripCount, 0) ?? 0
  const rate = bill?.summary.ratePerTonne ?? 0
  const gaps = evidenceGaps(bill?.missingEvidence)
  const final = phase === 'verified'
  const showWard = bill?.ward && bill.ward.trim().toLowerCase() !== 'ward'

  // ----------------------------------------------------------- the figure
  let figure = null
  if (bill && summary) {
    if (phase === 'pending') {
      figure = (
        <div className="figure">
          <div className="eyebrow">Contractor's claim</div>
          <div className="amount">
            ₹{lakh(summary.claimedRupees)}
            <small>lakh</small>
          </div>
          <p className="caption">
            for <em>{grouped(summary.claimedTonnes)} t</em> of silt at ₹{grouped(rate)} a tonne.
            Nothing has been checked yet.
          </p>
        </div>
      )
    } else {
      const flaggedRed = rows.filter((row) => row.verdict === 'RED').length
      figure = (
        <div className="figure held">
          <div className="eyebrow">{final ? 'Payment held by the evidence' : 'Held so far'}</div>
          <div className="amount">
            ₹{lakh(summary.heldRupees)}
            <small>lakh</small>
          </div>
          <p className="caption">
            {final ? (
              <>
                <em>{grouped(summary.heldTonnes)} t</em> of {grouped(summary.claimedTonnes)} t
                billed is contradicted by GPS, slips or photos, on {flaggedRed}{' '}
                {flaggedRed === 1 ? 'drain' : 'drains'}.
              </>
            ) : (
              <>
                from {revealed?.size ?? 0} of {bill.drains.length} drains checked.
              </>
            )}
          </p>
          {final && summary.heldTonnes > 0 && (
            <Stamp kind="held" big slam={props.slam} amount={`${grouped(summary.heldTonnes)} t`} />
          )}
        </div>
      )
    }
  }

  // -------------------------------------------------------------- actions
  const actions =
    phase === 'verified' && !props.verifying ? (
      <div className="reran">
        <span>
          Verified {trips} trips
          {bill?.verificationMs ? (
            <>
              {' '}
              in <span className="num">{Math.round(bill.verificationMs)} ms</span>
            </>
          ) : null}
          , 10 rules.
        </span>
        <button type="button" className="link" onClick={props.onVerify}>
          Run again
        </button>
      </div>
    ) : (
      <div className="actions">
        <button
          type="button"
          className={`btn btn-primary verify ${props.verifying ? 'is-busy' : ''}`}
          onClick={props.onVerify}
          disabled={!bill || props.verifying}
          aria-busy={props.verifying}
        >
          {props.verifying ? (
            <>
              <span className="spinner" aria-hidden="true" />
              <span>{props.verifyLabel}</span>
              <span
                className="bar-progress"
                style={{ transform: `scaleX(${props.verifyProgress})` }}
              />
            </>
          ) : (
            'Run verification'
          )}
        </button>
        <p className="hint">
          Checks all {trips || 'the'} trips against the ten rules. Textract and Bedrock read the
          slips and photos when they were uploaded; this step only runs the rules.
        </p>
      </div>
    )

  // ------------------------------------------------------------- register
  const decide = async (drainId: string, kind: Kind, note: string) => {
    const saved = await props.onDecide(drainId, kind, note)
    if (saved) {
      setConfirming(null)
      setOpenReview(null)
    }
  }

  const inlineReview = (row: DrainRow) => {
    const amount = rupees(row.reviewTonnes * rate)
    const busy = props.saving?.drainId === row.drainId
    const err = props.saveError?.drainId === row.drainId ? props.saveError.message : null
    if (confirming?.drainId === row.drainId && bill) {
      const kind = confirming.kind
      const preview = previewDecision(
        bill.drains,
        row.drainId,
        kind,
        rate,
        bill.summary.claimedTonnes,
      )
      return (
        <div className="inline">
          <ConfirmSheet
            title={`${kind === 'APPROVE' ? 'Approve' : 'Hold'} ${grouped(row.reviewTonnes)} t on drain ${row.drainId}?`}
            kind={kind}
            confirmLabel={kind === 'APPROVE' ? 'Confirm approval' : 'Confirm hold'}
            moves={[
              { label: kind === 'APPROVE' ? 'Moves to payable' : 'Moves to held', value: amount },
              { label: 'Bill verified after this', value: `${grouped(preview.after.verifiedTonnes)} t` },
              { label: 'Still for review', value: `${grouped(preview.after.reviewTonnes)} t` },
              { label: 'Held after this', value: `₹${lakh(preview.after.heldRupees)} L` },
            ]}
            busy={busy}
            error={err}
            onCancel={() => {
              setConfirming(null)
              props.onClearError()
            }}
            onConfirm={() => decide(row.drainId, kind, '')}
          >
            <p>{reasons[row.drainId] ?? 'A soft check failed on this drain.'}</p>
          </ConfirmSheet>
        </div>
      )
    }
    return (
      <div className="inline" id={`inline-${row.drainId}`}>
        <p>
          A soft check only, so it is your call: approve to pay the{' '}
          <span className="num">{grouped(row.reviewTonnes)} t</span>, or hold it.{' '}
          <button type="button" className="link" onClick={() => props.onOpen(row.drainId)}>
            Open the evidence
          </button>
        </p>
        <div className="two">
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setConfirming({ drainId: row.drainId, kind: 'APPROVE' })}
          >
            Approve {amount}
          </button>
          <button
            type="button"
            className="btn btn-quiet"
            onClick={() => setConfirming({ drainId: row.drainId, kind: 'HOLD' })}
          >
            Hold
          </button>
        </div>
      </div>
    )
  }

  let register = null
  if (bill && phase !== 'verified') {
    register = (
      <div className="register">
        <h2>
          <span>
            {bill.drains.length} drains on this bill
            {phase === 'pending' ? ', not checked' : ''}
          </span>
          <span>billed</span>
        </h2>
        <div className="grid18">
          {rows.map((row) => {
            const shown = revealed?.has(row.drainId)
            return (
              <button
                key={row.drainId}
                type="button"
                className={`cell ${shown ? 'flash' : ''} ${hoverId === row.drainId ? 'is-hover' : ''}`}
                data-drain={row.drainId}
                onMouseEnter={() => props.onHover(row.drainId)}
                onMouseLeave={() => props.onHover(null)}
                onClick={() => props.onOpen(row.drainId)}
              >
                <span className="no">{row.drainId}</span>
                <span className="cell-name">{row.name}</span>
                <span className="t">
                  {!shown ? (
                    <span className="mono">{grouped(row.claimedTonnes)} t</span>
                  ) : row.verdict === 'GREEN' ? (
                    <span className="ok">clear</span>
                  ) : row.verdict === 'RED' ? (
                    <b className="bad">held</b>
                  ) : (
                    <b className="soft">review</b>
                  )}
                </span>
              </button>
            )
          })}
        </div>
      </div>
    )
  } else if (bill) {
    const flagged = rows
      .filter((row) => row.verdict !== 'GREEN')
      .sort(
        (a, b) =>
          Number(b.verdict === 'RED') - Number(a.verdict === 'RED') ||
          b.heldTonnes - a.heldTonnes ||
          b.claimedTonnes - a.claimedTonnes,
      )
    const clear = rows.filter((row) => row.verdict === 'GREEN')

    register = (
      <div className="register">
        <h2>
          <span>Needs your decision</span>
          <span>amount</span>
        </h2>
        {flagged.map((row, index) => {
          const reviewOpen =
            openReview === row.drainId && row.verdict === 'AMBER' && !row.decision
          let cls: string
          let stamp
          let name: string
          if (row.decision === 'APPROVE') {
            const released = props.flaggedTonnes[row.drainId] ?? row.verifiedTonnes
            cls = 'approved'
            stamp = <Stamp kind="approved" amount={rupees(released * rate)} />
            name = `Approved by you, ${grouped(released)} t released`
          } else if (row.heldTonnes > 0) {
            cls = 'held'
            stamp = <Stamp kind="held" amount={rupees(row.heldTonnes * rate)} />
            name =
              row.decision === 'HOLD'
                ? `Held ${grouped(row.heldTonnes)} t, confirmed by you`
                : `Held ${grouped(row.heldTonnes)} of ${grouped(row.claimedTonnes)} t`
          } else {
            cls = 'review'
            stamp = <Stamp kind="review" amount={rupees(row.reviewTonnes * rate)} />
            name = `${grouped(row.reviewTonnes)} of ${grouped(row.claimedTonnes)} t for your review`
          }
          return (
            <div key={row.drainId} className="row-wrap">
              <button
                type="button"
                className={`row ${cls} ${props.enter ? 'enter' : ''} ${hoverId === row.drainId ? 'is-hover' : ''}`}
                style={props.enter ? { animationDelay: `${380 + index * 90}ms` } : undefined}
                data-drain={row.drainId}
                aria-expanded={row.verdict === 'AMBER' && !row.decision ? reviewOpen : undefined}
                onMouseEnter={() => props.onHover(row.drainId)}
                onMouseLeave={() => props.onHover(null)}
                onClick={() => {
                  if (row.verdict === 'AMBER' && !row.decision) {
                    setOpenReview(reviewOpen ? null : row.drainId)
                    setConfirming(null)
                    props.onClearError()
                  } else {
                    props.onOpen(row.drainId)
                  }
                }}
              >
                <span className="no">{row.drainId}</span>
                <span className="row-text">
                  <span className="name">{name}</span>
                  <span className="why">{reasons[row.drainId] ?? ' '}</span>
                </span>
                {stamp}
              </button>
              {reviewOpen && inlineReview(row)}
            </div>
          )
        })}
        <p className="clearline">
          {clear.length} drains pass every check: {clear.map((row) => row.drainId).join(', ')}.{' '}
          <span className="num">
            {grouped(clear.reduce((sum, row) => sum + row.claimedTonnes, 0))} t
          </span>{' '}
          payable as billed.
        </p>
      </div>
    )
  }

  return (
    <aside className="sheet" aria-label="Bill">
      <header className="masthead">
        <span className="wordmark">
          SiltProof <i>ward bill check</i>
        </span>
        <span className="masthead-end">
          {/* The judge trial is its own page (trial.html): no shared state with this bill. */}
          <a className="link" href={`${import.meta.env.BASE_URL}trial.html`}>
            Try SiltProof Yourself
          </a>
        </span>
      </header>

      {error && (
        <div className="err sheet-note" role="alert">
          <b>Could not reach the bill service.</b> {error}
        </div>
      )}
      {gaps && (
        <div className="warn sheet-note">
          Evidence gaps: {gaps}. Those trips are in review, not held.
        </div>
      )}

      {bill ? (
        <p className="bill-meta">
          <strong>Bill {bill.billId}</strong>
          {showWard ? `, ${bill.ward}` : ''}, drain desilting, {windowText(bill.workWindow)}
          <br />
          {bill.contractor}, {bill.drains.length} drains, {trips} trips
          {offline && (
            <span
              className="offline-tag"
              title="Decisions on this prepared case study are applied in this browser only and are not saved to AWS."
            >
              Decisions stay in this browser
            </span>
          )}
        </p>
      ) : (
        !error && (
          <div className="sheet-loading" aria-label="Loading the bill">
            <div className="skeleton" style={{ width: 260 }} />
            <div className="skeleton" style={{ width: 200 }} />
          </div>
        )
      )}

      {figure}
      {summary && <Ledger summary={summary} pending={phase === 'pending'} />}
      {actions}
      {register}
    </aside>
  )
}

export default BillSheet
