import type { Bill } from '../types'
import { offline } from '../api'
import { lakhLong, lakhShort, tonnes, useCountUp } from '../format'

interface Props {
  bill: Bill | null
  verifying: boolean
  animate: boolean
  onVerify: () => void
}

function SummaryBar({ bill, verifying, animate, onVerify }: Props) {
  const summary = bill?.summary
  const verified = bill?.status === 'VERIFIED'

  const verifiedTonnes = useCountUp(summary?.verifiedTonnes ?? 0, animate)
  const reviewTonnes = useCountUp(summary?.reviewTonnes ?? 0, animate)
  const heldRupees = useCountUp(summary?.heldRupees ?? 0, animate)

  return (
    <header className="summary-bar">
      <div className="brand">
        <span className="brand-name">SiltProof</span>
        <span className="brand-sub">
          {bill ? `${bill.ward || 'Ward'} · ${bill.contractor}` : 'loading…'}
        </span>
      </div>

      <dl className="stats">
        <div className="stat">
          <dt>Claimed</dt>
          <dd>{summary ? tonnes(summary.claimedTonnes) : '—'}</dd>
          <span className="stat-sub">{summary ? lakhShort(summary.claimedRupees) : ''}</span>
        </div>
        <div className="stat stat-verified">
          <dt>Verified</dt>
          <dd>{verified ? tonnes(verifiedTonnes) : '—'}</dd>
          <span className="stat-sub">
            {verified && summary ? lakhShort(summary.verifiedRupees) : 'not checked yet'}
          </span>
        </div>
        <div className="stat stat-review">
          <dt>Review</dt>
          <dd>{verified ? tonnes(reviewTonnes) : '—'}</dd>
          <span className="stat-sub">
            {verified && summary ? lakhShort(summary.reviewRupees) : ''}
          </span>
        </div>
        <div className="stat stat-hold">
          <dt>Hold</dt>
          <dd>{verified ? lakhLong(heldRupees) : '—'}</dd>
          <span className="stat-sub">
            {verified && summary ? tonnes(summary.heldTonnes) : ''}
          </span>
        </div>
      </dl>

      <button
        type="button"
        className="verify"
        onClick={onVerify}
        disabled={verifying || !bill}
      >
        {verifying ? 'Verifying…' : verified ? 'Re-run verification' : 'Run verification'}
      </button>

      <div className="badges">
        {offline && <span className="badge badge-offline">Offline snapshot</span>}
        <span className="badge">Simulated data</span>
      </div>
    </header>
  )
}

export default SummaryBar
