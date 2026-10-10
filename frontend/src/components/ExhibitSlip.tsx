import { useState } from 'react'

import Lightbox from './Lightbox'
import { offline } from '../api'
import type { CaseFacts } from '../lib/caseFacts'
import { LOUPE, SLIP_ROWS, SLIP_SIZE } from '../lib/slipLayout'
import { slipTitle } from '../lib/caseFacts'
import type { Finding, Trip } from '../types'

const SCAN_W = 222
const SLIP_RULES = ['R7', 'R8', 'R9', 'SLIP_MISSING']
// The snapshot's slip fields come from the dataset, not a live Textract read.
const READ_CAPTION = offline
  ? 'Fields recorded for this slip, with confidence:'
  : 'Fields as Textract read them at upload, with its confidence:'

interface Props {
  trip: Trip | null
  facts: CaseFacts
  imageExpired: boolean
  /** The prepared investigation: its slips are generated. */
  simulatedCase: boolean
  onImageError: () => void
}

function Field({ trip, name, label, hit }: { trip: Trip; name: string; label: string; hit: boolean }) {
  const slip = trip.slip!
  const field = slip.fields?.[name]
  const raw = (slip as unknown as Record<string, unknown>)[name]
  let value: string
  if (raw === null || raw === undefined || raw === '') value = 'not read'
  else if (typeof raw === 'number') value = `${raw.toFixed(2)} t`
  else value = String(raw)
  const low = slip.lowConfidenceFields.includes(name)
  return (
    <div className={`${hit ? 'hit' : ''} ${low ? 'low' : ''}`}>
      <span>{label}</span>
      <b className="mono">{value}</b>
      <em className="mono" title="Confidence">
        {field?.confidence ? `${field.confidence.toFixed(1)}%` : ''}
      </em>
    </div>
  )
}

function ExhibitSlip({ trip, facts, imageExpired, simulatedCase, onImageError }: Props) {
  const [enlarged, setEnlarged] = useState(false)
  const slip = trip?.slip ?? null
  const url = trip?.slipImageUrl ?? null
  const hit = facts.conflictingField
  const findings: Finding[] = (trip?.findings ?? []).filter((f) => SLIP_RULES.includes(f.rule))
  const row = hit ? SLIP_ROWS[hit] : null
  const loupe = hit ? LOUPE[hit] : null

  return (
    <article className="exhibit" data-shot="slip" aria-labelledby="exhibit-b">
      <div className="ex-head">
        <span className="ex-tag">Exhibit B</span>
        <h2 id="exhibit-b">{slipTitle(facts)}</h2>
        <span className="rules">
          {findings.map((f) => (
            <span key={f.rule} className={`rid ${f.severity === 'soft' ? 'soft' : ''}`}>
              {f.rule}
            </span>
          ))}
        </span>
      </div>

      {!trip ? (
        <p className="quiet-note">This drain has no trips on the bill.</p>
      ) : !slip ? (
        <div className="warn">
          No weighbridge slip was ingested for trip {trip.tripNo}. The trip is in review, not held.
        </div>
      ) : (
        <div className="slipgrid">
          {url && !imageExpired ? (
            <figure className="scan">
              <button
                type="button"
                className="scan-inner"
                onClick={() => setEnlarged(true)}
                aria-label={`Enlarge the weighbridge slip for trip ${trip.tripNo}`}
              >
                <img
                  src={url}
                  width={SCAN_W}
                  alt={`Weighbridge slip for trip ${trip.tripId}, ticket ${slip.ticketNo ?? 'unread'}, time in ${slip.timeIn ?? 'unread'}`}
                  onError={onImageError}
                />
                {row && (
                  <span
                    className="hl"
                    style={{ top: `${(row[0] / SLIP_SIZE.height) * 100}%`, height: `${(row[1] / SLIP_SIZE.height) * 100}%` }}
                    aria-hidden="true"
                  />
                )}
              </button>
              <figcaption>
                Trip {trip.tripNo}, ticket <span className="mono">{slip.ticketNo ?? 'unread'}</span>
              </figcaption>
            </figure>
          ) : (
            <div className="photoslot slip-slot">
              <b>{imageExpired ? 'Slip link expired' : 'Slip image unavailable'}</b>
              <span>
                {imageExpired
                  ? 'Reopen the drain to refresh it.'
                  : 'The fields read from the slip are shown alongside.'}
              </span>
            </div>
          )}

          <div className="slip-read">
            {url && !imageExpired && loupe && (
              <>
                <div
                  className="loupe"
                  role="img"
                  aria-label={`Enlarged from the slip: ${hit === 'timeIn' ? `time in ${slip.timeIn}, time out ${slip.timeOut}` : hit === 'net' ? `net weight ${slip.net} t` : `vehicle ${slip.vehicleNo}`}`}
                  style={{ aspectRatio: `696 / ${loupe[1]}` }}
                >
                  <img
                    src={url}
                    alt=""
                    style={{
                      width: `${(SLIP_SIZE.width / 696) * 100}%`,
                      // Percentages of the image's own size, so the crop is exact.
                      transform: `translate(${(-30 / SLIP_SIZE.width) * 100}%, ${(-loupe[0] / SLIP_SIZE.height) * 100}%)`,
                    }}
                  />
                  {row && (
                    <span
                      className="hl"
                      style={{
                        top: `${((row[0] - loupe[0]) / loupe[1]) * 100}%`,
                        height: `${(row[1] / loupe[1]) * 100}%`,
                      }}
                    />
                  )}
                </div>
                <p className="loupe-cap">Enlarged from the slip. {READ_CAPTION}</p>
              </>
            )}
            {(!url || imageExpired || !loupe) && (
              <p className="loupe-cap">{READ_CAPTION}</p>
            )}
            <div className="fields">
              <Field trip={trip} name="ticketNo" label="Ticket" hit={hit === 'ticketNo'} />
              <Field trip={trip} name="timeIn" label="Time in" hit={hit === 'timeIn'} />
              <Field trip={trip} name="vehicleNo" label="Vehicle" hit={hit === 'vehicleNo'} />
              <Field trip={trip} name="timeOut" label="Time out" hit={hit === 'timeOut'} />
              <Field trip={trip} name="net" label="Net" hit={hit === 'net'} />
              <Field trip={trip} name="gross" label="Gross" hit={hit === 'gross'} />
            </div>
            {findings.map((f) => (
              <div key={f.rule + f.message} className={`r8note ${f.severity}`}>
                <span className="k">
                  {f.rule}, {f.severity === 'hard' ? 'hard' : 'soft'} check failed
                </span>
                {f.message}
              </div>
            ))}
            {findings.length === 0 && (
              <p className="pass-note">The slip agrees with trip {trip.tripNo}: R7, R8 and R9 passed.</p>
            )}
          </div>
        </div>
      )}
      {simulatedCase && slip && (
        <p className="ex-note">
          Generated slip from a fictional weighbridge.
          {offline ? ' Its fields and confidence scores are part of the simulated dataset.' : ''}
        </p>
      )}
      {enlarged && url && slip && trip && (
        <Lightbox
          label={`Weighbridge slip, trip ${trip.tripNo}`}
          onClose={() => setEnlarged(false)}
          items={[
            {
              src: url,
              alt: `Weighbridge slip for trip ${trip.tripId}, ticket ${slip.ticketNo ?? 'unread'}`,
              highlight: row ? { top: (row[0] / SLIP_SIZE.height) * 100, height: (row[1] / SLIP_SIZE.height) * 100 } : null,
              caption: findings[0]?.message ?? `Trip ${trip.tripNo}: the slip agrees with the trip.`,
            },
          ]}
        />
      )}
    </article>
  )
}

export default ExhibitSlip
