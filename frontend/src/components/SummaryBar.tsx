import type { Bill } from '../types'
import { offline } from '../api'

function lakh(rupees: number) {
  return `₹${(rupees / 100000).toFixed(2)} L`
}

function tonnes(value: number) {
  return `${value.toLocaleString('en-IN', { maximumFractionDigits: 0 })} t`
}

interface Props {
  bill: Bill | null
  verifying: boolean
  onVerify: () => void
}

function SummaryBar({ bill, verifying, onVerify }: Props) {
  const summary = bill?.summary
  const verified = bill?.status === 'VERIFIED'

  return (
    <header className="summary-bar">
      <div className="brand">
        <span className="brand-name">SiltProof</span>
        <span className="brand-sub">
          {bill ? `${bill.ward} · ${bill.contractor}` : 'loading…'}
        </span>
      </div>

      <dl className="stats">
        <div className="stat">
          <dt>Claimed</dt>
          <dd>{summary ? tonnes(summary.claimedTonnes) : '—'}</dd>
          <span className="stat-sub">{summary ? lakh(summary.claimedRupees) : ''}</span>
        </div>
        <div className="stat stat-verified">
          <dt>Verified</dt>
          <dd>{verified && summary ? tonnes(summary.verifiedTonnes) : '—'}</dd>
          <span className="stat-sub">
            {verified && summary ? lakh(summary.verifiedRupees) : ''}
          </span>
        </div>
        <div className="stat stat-review">
          <dt>Review</dt>
          <dd>{verified && summary ? tonnes(summary.reviewTonnes) : '—'}</dd>
          <span className="stat-sub">
            {verified && summary ? lakh(summary.reviewRupees) : ''}
          </span>
        </div>
        <div className="stat stat-hold">
          <dt>Hold</dt>
          <dd>{verified && summary ? lakh(summary.heldRupees) : '—'}</dd>
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
