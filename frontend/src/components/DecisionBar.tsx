import { useState } from 'react'

import ConfirmSheet from './ConfirmSheet'
import Stamp from './Stamp'
import { grouped, lakh, rupees } from '../format'
import { previewDecision } from '../lib/ledger'
import type { Kind } from '../lib/ledger'
import type { CaseFacts } from '../lib/caseFacts'
import type { Bill, Drain, DrainRow } from '../types'

const WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten']
const SITE_NOTE_MIN = 20
const OFFLINE_PREFIX = /^Offline summary, no model was called\.\s*/

function listRules(rules: string[]) {
  if (rules.length <= 1) return rules.join('')
  return `${rules.slice(0, -1).join(', ')} and ${rules[rules.length - 1]}`
}

function recorded(iso: string | null) {
  if (!iso) return 'Recorded just now'
  return `Recorded ${new Date(iso).toLocaleString('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
    timeZone: 'Asia/Kolkata',
  })}`
}

interface Props {
  bill: Bill
  drain: Drain
  row: DrainRow
  facts: CaseFacts
  verified: boolean
  /** Tonnes the evidence flagged on this drain: what an approval releases. */
  flaggedTonnes: number
  decidedAt: string | null
  saving: Kind | null
  saveError: string | null
  explaining: boolean
  evidenceSummary: string | null
  onExplain: () => void
  onDecide: (kind: Kind, note: string) => Promise<boolean>
  onClearError: () => void
}

function DecisionBar(props: Props) {
  const { bill, drain, row, facts, verified } = props
  const [note, setNote] = useState('')
  const [confirming, setConfirming] = useState<Kind | null>(null)
  const [changing, setChanging] = useState(false)
  const rate = bill.summary.ratePerTonne

  const held = row.heldTonnes
  const flagged = props.flaggedTonnes
  const againstEvidence = facts.hardRules.length > 0

  // ---------------------------------------------------------- the summary
  const raw = props.evidenceSummary ?? drain.summary
  const offlineText = raw ? OFFLINE_PREFIX.test(raw) : false
  const summary = (
    <div className="summary">
      <span className="k">
        {!raw
          ? 'Evidence summary'
          : offlineText
            ? 'Evidence summary, offline: assembled from the rule findings, no model was called'
            : `Evidence summary written by ${drain.summaryModelId ?? 'Amazon Bedrock'}. A description, not a finding`}
      </span>
      {raw ? (
        <p title={raw.replace(OFFLINE_PREFIX, '')}>{raw.replace(OFFLINE_PREFIX, '')}</p>
      ) : verified && drain.verdict !== 'GREEN' ? (
        <p>
          <button type="button" className="link" onClick={props.onExplain} disabled={props.explaining}>
            {props.explaining ? 'Asking Bedrock…' : 'Write a two-line summary'}
          </button>
        </p>
      ) : (
        <p>{verified ? 'Every check passed on this drain.' : 'Not checked yet. Run verification first.'}</p>
      )}
    </div>
  )

  // ------------------------------------------------------- decided state
  if (row.decision && !changing) {
    const approved = row.decision === 'APPROVE'
    const amount = approved ? flagged * rate : held * rate
    return (
      <section className="decide" aria-label="Your decision">
        {summary}
        <div className="done">
          <Stamp kind={approved ? 'approved' : 'held'} big slam amount={rupees(amount)} />
          <div>
            <p>
              <b>
                {approved
                  ? `You released ${rupees(amount)} on drain ${drain.drainId}.`
                  : `You held ${rupees(amount)} on drain ${drain.drainId}.`}
              </b>
              <br />
              {recorded(props.decidedAt)} by the ward engineer.
            </p>
            <p className="note">Note: {row.note || drain.note || 'none'}</p>
          </div>
          <button type="button" className="link" onClick={() => setChanging(true)}>
            Change decision
          </button>
        </div>
      </section>
    )
  }

  // ------------------------------------------------------- confirmation
  let sheet = null
  if (confirming) {
    const preview = previewDecision(bill.drains, drain.drainId, confirming, rate, bill.summary.claimedTonnes)
    const close = () => {
      setConfirming(null)
      props.onClearError()
    }
    const submit = async (siteNote: string | null) => {
      const text = [siteNote, note.trim()].filter(Boolean).join(' ')
      const saved = await props.onDecide(confirming, text)
      if (saved) {
        setConfirming(null)
        setChanging(false)
        setNote('')
      }
    }
    const busy = props.saving === confirming
    if (confirming === 'HOLD') {
      const amount = rupees((row.reviewTonnes + row.heldTonnes) * rate)
      sheet = (
        <ConfirmSheet
          floating
          title={`Hold ${amount} on drain ${drain.drainId}?`}
          kind="HOLD"
          confirmLabel="Confirm hold"
          moves={[
            { label: 'Held on this drain', value: amount },
            { label: 'Whole bill held after this', value: `₹${lakh(preview.after.heldRupees)} L` },
          ]}
          busy={busy}
          error={props.saveError}
          onCancel={close}
          onConfirm={submit}
        >
          <p>
            The contractor is not paid for these {grouped(row.reviewTonnes + row.heldTonnes)} t until the hold
            is lifted. Your note goes on the file.
          </p>
        </ConfirmSheet>
      )
    } else {
      const amount = rupees(flagged * rate)
      const n = facts.hardRules.length
      sheet = (
        <ConfirmSheet
          floating
          title={
            againstEvidence
              ? `Release ${amount} despite ${WORDS[n] ?? n} failed hard ${n === 1 ? 'check' : 'checks'}?`
              : `Approve ${grouped(flagged)} t on drain ${drain.drainId}?`
          }
          kind="APPROVE"
          confirmLabel={againstEvidence ? 'Approve and release' : 'Confirm approval'}
          requireNote={againstEvidence ? { label: 'Site note, required to approve against the evidence', min: SITE_NOTE_MIN } : null}
          moves={[
            { label: 'Moves to payable', value: amount },
            { label: 'Whole bill held after this', value: `₹${lakh(preview.after.heldRupees)} L` },
          ]}
          busy={busy}
          error={props.saveError}
          onCancel={close}
          onConfirm={submit}
        >
          <p>
            {againstEvidence
              ? `Approving overrules ${listRules(facts.hardRules)} on ${facts.holdTrips} of ${drain.trips.length} trips. Record what you saw on site that the evidence does not show.`
              : 'Only soft checks failed, so this is your call. The note goes on the file.'}
          </p>
        </ConfirmSheet>
      )
    }
  }

  const disabled = !verified || props.saving !== null
  const holdFirst = held > 0
  const holdLabel = holdFirst ? `Hold ${rupees(held * rate)}` : 'Hold'
  const approveLabel = holdFirst
    ? 'Approve and release'
    : row.reviewTonnes > 0
      ? `Approve ${rupees(row.reviewTonnes * rate)}`
      : 'Approve'

  return (
    <section className="decide" aria-label="Your decision">
      {summary}
      <div className="decide-note">
        <label htmlFor="decision-note">Note for the file</label>
        <textarea
          id="decision-note"
          value={note}
          disabled={disabled}
          placeholder="Optional. Why you approved or held this drain"
          onChange={(event) => setNote(event.target.value)}
        />
      </div>
      <div className="decide-actions">
        {!verified && <p className="need-verify">Run verification before deciding.</p>}
        <div className="btns">
          {holdFirst ? (
            <>
              <button type="button" className="btn btn-held" disabled={disabled} onClick={() => setConfirming('HOLD')}>
                {holdLabel}
              </button>
              <button type="button" className="btn btn-quiet" disabled={disabled} onClick={() => setConfirming('APPROVE')}>
                {approveLabel}
              </button>
            </>
          ) : (
            <>
              <button type="button" className="btn btn-primary" disabled={disabled} onClick={() => setConfirming('APPROVE')}>
                {approveLabel}
              </button>
              <button type="button" className="btn btn-quiet" disabled={disabled} onClick={() => setConfirming('HOLD')}>
                {holdLabel}
              </button>
            </>
          )}
        </div>
        {changing && (
          <button type="button" className="link" onClick={() => setChanging(false)}>
            Keep the current decision
          </button>
        )}
      </div>
      {sheet}
    </section>
  )
}

export default DecisionBar
