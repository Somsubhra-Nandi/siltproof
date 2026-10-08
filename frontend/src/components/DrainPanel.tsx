import { useState } from 'react'

import type { Drain, Slip, Trip } from '../types'

interface Props {
  drain: Drain | null
  loading: boolean
  error: string | null
  selectedTripId: string | null
  onSelectTrip: (tripId: string) => void
  onDecide: (decision: 'APPROVE' | 'HOLD', note: string) => void
  onExplain: () => void
  explaining: boolean
  evidenceSummary: string | null
  deciding: boolean
  onClose: () => void
}

function tonnes(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : `${value.toFixed(1)} t`
}

/** The slip field a rule is arguing with, so it can be highlighted. */
function conflictingField(trip: Trip): string | null {
  const rules = [...trip.hardFails, ...trip.softFails]
  if (rules.includes('R7')) return 'net'
  if (rules.includes('R8')) return 'timeIn'
  if (rules.includes('R9')) return 'vehicleNo'
  return null
}

function SlipTable({ slip, highlight }: { slip: Slip; highlight: string | null }) {
  const rows: Array<[string, string, string | number | null]> = [
    ['Ticket', 'ticketNo', slip.ticketNo],
    ['Vehicle', 'vehicleNo', slip.vehicleNo],
    ['Gross', 'gross', slip.gross],
    ['Tare', 'tare', slip.tare],
    ['Net', 'net', slip.net],
    ['Time in', 'timeIn', slip.timeIn],
    ['Time out', 'timeOut', slip.timeOut],
  ]

  return (
    <table className="slip-table">
      <tbody>
        {rows.map(([label, key, value]) => {
          const field = slip.fields?.[key]
          const missing = slip.missingFields.includes(key)
          return (
            <tr
              key={key}
              className={[
                highlight === key ? 'conflict' : '',
                missing ? 'missing' : '',
              ].join(' ')}
            >
              <th>{label}</th>
              <td>{value === null || value === undefined ? '— not read' : String(value)}</td>
              <td className="confidence">
                {field?.confidence ? `${field.confidence.toFixed(0)}%` : ''}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function DrainPanel({
  drain,
  loading,
  error,
  selectedTripId,
  onSelectTrip,
  onDecide,
  onExplain,
  explaining,
  evidenceSummary,
  deciding,
  onClose,
}: Props) {
  const [note, setNote] = useState('')

  if (loading) {
    return (
      <aside className="panel">
        <p className="panel-empty">Loading drain…</p>
      </aside>
    )
  }

  if (error) {
    return (
      <aside className="panel">
        <p className="panel-error">{error}</p>
      </aside>
    )
  }

  if (!drain) {
    return (
      <aside className="panel">
        <p className="panel-empty">
          Pick a drain on the map to see its evidence.
        </p>
      </aside>
    )
  }

  const trip = drain.trips.find((entry) => entry.tripId === selectedTripId) ?? drain.trips[0]
  const flagged = drain.trips.filter((entry) => entry.verdict !== 'VERIFIED')
  const highlight = trip ? conflictingField(trip) : null

  const tripFindings = trip?.findings ?? []
  const allFindings = [
    ...drain.findings,
    ...tripFindings.filter(
      (item) => !drain.findings.some((other) => other.rule === item.rule),
    ),
  ]

  return (
    <aside className="panel">
      <div className="panel-head">
        <div>
          <h2>
            Drain {drain.drainId}
            <span className={`verdict verdict-${drain.verdict ?? 'pending'}`}>
              {drain.verdict ?? 'not verified'}
            </span>
          </h2>
          <p className="panel-sub">
            {drain.name} · {drain.lengthM?.toFixed(0)} m × {drain.widthM} m ×{' '}
            {drain.depthM} m · {drain.trips.length} trips
          </p>
        </div>
        <button type="button" className="close" onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>

      <dl className="panel-stats">
        <div>
          <dt>Claimed</dt>
          <dd>{tonnes(drain.claimedTonnes)}</dd>
        </div>
        <div>
          <dt>Verified</dt>
          <dd className="good">{tonnes(drain.verifiedTonnes)}</dd>
        </div>
        <div>
          <dt>Review</dt>
          <dd className="warn">{tonnes(drain.reviewTonnes)}</dd>
        </div>
        <div>
          <dt>Hold</dt>
          <dd className="bad">{tonnes(drain.heldTonnes)}</dd>
        </div>
      </dl>

      {/* ---- what the rules found */}
      <section>
        <h3>
          Why{' '}
          <span className="muted">
            {allFindings.length ? `${allFindings.length} findings` : 'nothing was flagged'}
          </span>
        </h3>

        {allFindings.length === 0 && (
          <p className="clean-note">Every check passed on this drain.</p>
        )}

        <ul className="findings">
          {allFindings.map((item, index) => (
            <li key={`${item.rule}-${index}`} className={`finding finding-${item.severity}`}>
              <span className="rule-id">{item.rule}</span>
              <div>
                <p className="finding-message">{item.message}</p>
                <p className="finding-rule">{item.rule_text}</p>
              </div>
            </li>
          ))}
        </ul>

        {allFindings.length > 0 && (
          <div className="explain">
            <button type="button" onClick={onExplain} disabled={explaining}>
              {explaining ? 'Asking Bedrock…' : 'Explain in two lines'}
            </button>
            {evidenceSummary && <p className="evidence-summary">{evidenceSummary}</p>}
          </div>
        )}
      </section>

      {/* ---- the trips */}
      {drain.trips.length > 0 && (
        <section>
          <h3>
            Trips <span className="muted">{flagged.length} flagged</span>
          </h3>
          <div className="trip-chips">
            {drain.trips.map((entry) => (
              <button
                key={entry.tripId}
                type="button"
                onClick={() => onSelectTrip(entry.tripId)}
                className={[
                  'trip-chip',
                  `trip-${(entry.verdict ?? 'pending').toLowerCase()}`,
                  entry.tripId === trip?.tripId ? 'trip-selected' : '',
                ].join(' ')}
                title={`${entry.vehicleNo} · ${entry.claimedTonnes} t`}
              >
                {entry.tripNo}
              </button>
            ))}
          </div>

          {trip && (
            <div className="trip-detail">
              <p className="trip-line">
                <strong>{trip.vehicleNo}</strong> · {trip.claimedTonnes} t ·{' '}
                {(trip.actualRouteDistanceM / 1000).toFixed(1)} km driven ·{' '}
                {trip.tracePointCount} GPS points
              </p>
              {trip.traceProblem && (
                <p className="warn-line">GPS trace unreadable: {trip.traceProblem}</p>
              )}
              {trip.slip ? (
                <SlipTable slip={trip.slip} highlight={highlight} />
              ) : (
                <p className="warn-line">No weighbridge slip was ingested for this trip.</p>
              )}
            </div>
          )}
        </section>
      )}

      {/* ---- the photos */}
      {drain.photos.length > 0 && (
        <section>
          <h3>
            Photos <span className="muted">{drain.photos.length}</span>
          </h3>
          <ul className="photos">
            {drain.photos.map((photo) => (
              <li key={photo.s3Key} className="photo">
                <div className="photo-head">
                  <span className="photo-role">{photo.role}</span>
                  <span className="photo-key">{photo.s3Key.split('/').pop()}</span>
                </div>
                <p className="photo-line">
                  {photo.hasGps
                    ? `${photo.lat?.toFixed(5)}, ${photo.lon?.toFixed(5)}`
                    : 'no GPS'}{' '}
                  · {photo.timestamp?.slice(0, 16).replace('T', ' ') ?? 'no timestamp'}
                </p>
                <p className="photo-line">
                  <span className={`chip chip-${photo.bedrock.loadType ?? 'unclear'}`}>
                    {photo.bedrock.loadType}
                  </span>
                  {photo.bedrock.cleared ? ' cleared' : ' not cleared'}
                  {photo.bedrock.confidence !== null &&
                    ` · ${(photo.bedrock.confidence * 100).toFixed(0)}% confident`}
                </p>
                {photo.bedrock.notes && <p className="photo-notes">{photo.bedrock.notes}</p>}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* ---- the decision */}
      <section className="decide">
        <h3>Decision</h3>
        {drain.decision ? (
          <p className="decided">
            {drain.decision === 'APPROVE' ? 'Approved' : 'Held'}
            {drain.note ? ` — ${drain.note}` : ''}
          </p>
        ) : (
          <p className="muted">Not decided yet.</p>
        )}

        <textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Note (optional) — why you approved or held this drain"
          rows={2}
        />
        <div className="decide-buttons">
          <button
            type="button"
            className="approve"
            disabled={deciding || drain.verdict === null}
            onClick={() => onDecide('APPROVE', note)}
          >
            Approve
          </button>
          <button
            type="button"
            className="hold"
            disabled={deciding || drain.verdict === null}
            onClick={() => onDecide('HOLD', note)}
          >
            Hold payment
          </button>
        </div>
        {drain.verdict === null && (
          <p className="muted">Run verification before deciding.</p>
        )}
      </section>
    </aside>
  )
}

export default DrainPanel
