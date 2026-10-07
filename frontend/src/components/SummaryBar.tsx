// Placeholder numbers until GET /bill/{billId} is wired up on Day 2.
// They match the demo totals in the plan (section 6).
const placeholder = {
  ward: 'Ward placeholder',
  contractor: 'Contractor placeholder',
  claimedTonnes: 1240,
  verifiedTonnes: 870,
  heldRupees: 666000,
}

function lakh(rupees: number) {
  return `₹${(rupees / 100000).toFixed(2)} lakh`
}

function tonnes(value: number) {
  return `${value.toLocaleString('en-IN')} t`
}

function SummaryBar() {
  return (
    <header className="summary-bar">
      <div className="brand">
        <span className="brand-name">SiltProof</span>
        <span className="brand-sub">
          {placeholder.ward} · {placeholder.contractor}
        </span>
      </div>

      <dl className="stats">
        <div className="stat">
          <dt>Claimed</dt>
          <dd>{tonnes(placeholder.claimedTonnes)}</dd>
        </div>
        <div className="stat stat-verified">
          <dt>Verified</dt>
          <dd>{tonnes(placeholder.verifiedTonnes)}</dd>
        </div>
        <div className="stat stat-hold">
          <dt>Hold</dt>
          <dd>{lakh(placeholder.heldRupees)}</dd>
        </div>
      </dl>

      <span className="sim-label">Simulated data</span>
    </header>
  )
}

export default SummaryBar
