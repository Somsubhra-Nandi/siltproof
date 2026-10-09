import { STATUS_LABEL, sectionChecks } from './logic'
import type { Analysis, Check, Evidence, Observation, Trial } from './types'

type Props = {
  trial: Trial
  analysis: Analysis
  onBack: () => void
  onReanalyze: () => void
  analyzing: boolean
}

const BASIS: Record<string, string> = {
  approximate: 'against a supplied, approximate location',
  supplied: 'on details supplied for this trial',
  extracted: 'on values read from the files',
  model: 'on the model’s reading',
  mocked: 'on offline mock values',
}

function statusClass(check: Check) {
  return `jt-status s-${check.status.toLowerCase()}`
}

function CheckRow({ check }: { check: Check }) {
  return (
    <li className={`jt-check-row s-${check.status.toLowerCase()}`}>
      <div className="jt-check-head">
        <span className={`rid${check.status === 'FAIL' ? '' : check.status === 'REVIEW' ? ' soft' : check.status === 'PASS' ? ' pass' : ' muted'}`}>{check.id}</span>
        <span className="jt-check-title">
          {check.title}
          {check.subject?.filename && <small> · {check.subject.filename}</small>}
        </span>
        <span className={statusClass(check)}>{STATUS_LABEL[check.status]}</span>
      </div>
      <p className="jt-check-msg">{check.message}</p>
      {check.missing.length > 0 && (
        <p className="jt-check-missing">Needs: {check.missing.join('; ')}.</p>
      )}
      {check.basis && check.status !== 'NOT_EVALUATED' && (
        <p className="jt-check-basis">Checked {BASIS[check.basis] ?? check.basis}.</p>
      )}
    </li>
  )
}

function VisionCard({ obs, evidence }: { obs: Observation; evidence?: Evidence }) {
  const vision = obs.vision
  const exif = obs.exif
  return (
    <figure className="jt-obs">
      {evidence?.previewUrl ? (
        <a href={evidence.previewUrl} target="_blank" rel="noreferrer">
          <img src={evidence.previewUrl} alt={`Uploaded photo ${obs.filename ?? ''}`} loading="lazy" />
        </a>
      ) : (
        <div className="jt-obs-noimg">Preview link expired; reload the trial to refresh it.</div>
      )}
      <figcaption>
        <b>{obs.filename}</b> <span className="jt-role">{obs.role}</span>
        <dl className="jt-dl">
          <dt>Taken</dt><dd>{exif?.timestamp ? `${exif.timestamp.replace('T', ' ')}${exif.timestampHasOffset ? '' : ' (no timezone in EXIF)'}` : 'not recorded'}</dd>
          <dt>GPS</dt><dd>{exif?.hasGps ? `${exif.lat?.toFixed(6)}, ${exif.lon?.toFixed(6)}` : 'not recorded'}{exif?.hasGps && (exif.gpsAccuracyM ? ` (±${exif.gpsAccuracyM} m)` : ' (accuracy not recorded)')}</dd>
          <dt>Camera</dt><dd>{exif?.camera ?? '—'}</dd>
          <dt>pHash</dt><dd className="mono">{obs.pHash ?? '—'}</dd>
        </dl>
        {vision && (
          <div className={`jt-ai-box${vision.mocked ? ' mocked' : ''}`}>
            <p className="jt-ai-cap">
              {vision.mocked ? 'Offline mock, not a model result' : 'AI observation, not a finding'}
              {vision.modelId ? ` · ${vision.modelId}` : ''}
              {obs.processingCopy ? ` · read from a ${obs.processingCopy.copyPixels.join('×')} copy of the ${obs.processingCopy.originalPixels.join('×')} original` : ''}
            </p>
            {vision.ran ? (
              <>
                <p className="jt-ai-text">“{vision.notes}”</p>
                <p className="jt-ai-fields">
                  cleared: <b>{String(vision.cleared)}</b> · load: <b>{vision.loadType}</b> · confidence: <b>{vision.confidence}</b>
                  {vision.ok ? '' : ' · answer did not fit the expected form'}
                </p>
              </>
            ) : (
              <p className="jt-ai-text">Not read by the model: {vision.skippedReason}. {vision.notes}</p>
            )}
          </div>
        )}
      </figcaption>
    </figure>
  )
}

const SLIP_FIELDS: Array<[string, string]> = [
  ['ticketNo', 'Ticket'], ['vehicleNo', 'Vehicle'], ['gross', 'Gross, t'], ['tare', 'Tare, t'],
  ['net', 'Net, t'], ['timeIn', 'Time in'], ['timeOut', 'Time out'], ['site', 'Site'],
]

function SlipCard({ obs, evidence }: { obs: Observation; evidence?: Evidence }) {
  const fields = obs.fields ?? {}
  return (
    <figure className="jt-obs">
      {evidence?.previewUrl && evidence.contentType !== 'application/pdf' ? (
        <a href={evidence.previewUrl} target="_blank" rel="noreferrer">
          <img src={evidence.previewUrl} alt={`Uploaded slip ${obs.filename ?? ''}`} loading="lazy" />
        </a>
      ) : evidence?.previewUrl ? (
        <a className="jt-obs-noimg" href={evidence.previewUrl} target="_blank" rel="noreferrer">Open the PDF slip</a>
      ) : null}
      <figcaption>
        <b>{obs.filename}</b>
        <p className={`jt-ai-cap${obs.mocked ? ' mocked' : ''}`}>
          {obs.mocked ? 'Offline mock, not read by Textract' : `Amazon Textract · average confidence ${obs.confidenceAvg}%`}
        </p>
        <table className="jt-slip">
          <thead><tr><th>Field</th><th>Read as</th><th>Raw text</th><th>Confidence</th></tr></thead>
          <tbody>
            {SLIP_FIELDS.map(([key, label]) => {
              const field = fields[key]
              return (
                <tr key={key} className={!field?.ok ? 'missing' : field.lowConfidence ? 'low' : ''}>
                  <td>{label}</td>
                  <td className="mono">{field?.value ?? 'unreadable'}{field?.derived ? ' (gross − tare)' : ''}</td>
                  <td>{field?.raw ?? '—'}</td>
                  <td className="mono">{field ? `${field.confidence}%` : '—'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </figcaption>
    </figure>
  )
}

function TraceCard({ obs }: { obs: Observation }) {
  return (
    <figure className="jt-obs text">
      <figcaption>
        <b>{obs.filename}</b>
        <dl className="jt-dl">
          <dt>Vehicle</dt><dd>{obs.vehicleNo ?? 'not given'}</dd>
          <dt>Points</dt><dd>{obs.pointCount}</dd>
          <dt>From</dt><dd>{obs.startTime}</dd>
          <dt>To</dt><dd>{obs.endTime}</dd>
          <dt>Length</dt><dd>{((obs.distanceM ?? 0) / 1000).toFixed(2)} km</dd>
          <dt>Longest gap</dt><dd>{obs.maxGapSeconds != null ? `${obs.maxGapSeconds} s` : '—'}</dd>
        </dl>
        {(obs.warnings ?? []).map((warning) => <p key={warning} className="jt-warn">{warning}</p>)}
      </figcaption>
    </figure>
  )
}

export default function ResultsScreen({ trial, analysis, onBack, onReanalyze, analyzing }: Props) {
  const sections = sectionChecks(analysis.checks)
  const byId = new Map(trial.evidence.map((item) => [item.evidenceId, item]))
  const counts = analysis.counts

  return (
    <section className="jt-results" aria-labelledby="jt-results-title">
      <div className="jt-results-head">
        <p className="jt-eyebrow">Trial results · {new Date(analysis.analyzedAt).toLocaleString()}</p>
        <h1 id="jt-results-title" className="jt-title">What the evidence supports</h1>
        {analysis.provenance.mocked && (
          <p className="jt-callout warn" role="note">
            Offline mock run: photo and slip readings came from test fixtures, not from Amazon Bedrock
            or Textract. Checks that depend on them are marked not evaluated.
          </p>
        )}
        <p className="jt-summary">{analysis.summary.text}</p>
        <p className="jt-note">Summary written by a fixed template from the checks below; no model wrote it.</p>
        <ul className="jt-counts" aria-label="Check counts">
          <li className="c-fail"><b>{counts.FAIL}</b> failed</li>
          <li className="c-review"><b>{counts.REVIEW}</b> review</li>
          <li className="c-pass"><b>{counts.PASS}</b> passed</li>
          <li className="c-consistent"><b>{counts.CONSISTENT}</b> consistent</li>
          <li className="c-inconclusive"><b>{counts.INCONCLUSIVE}</b> inconclusive</li>
          <li className="c-missing"><b>{counts.NOT_EVALUATED}</b> not evaluated</li>
        </ul>
        <div className="jt-results-actions">
          <button type="button" className="btn btn-quiet" onClick={onBack}>Add or change evidence</button>
          <button type="button" className={`btn btn-quiet${analyzing ? ' is-busy' : ''}`} onClick={onReanalyze} disabled={analyzing}>
            {analyzing && <span className="spinner" aria-hidden />}Run again
          </button>
        </div>
      </div>

      {sections.map((section) => (
        <div key={section.key} className={`jt-section sec-${section.key}`}>
          <h2 className="jt-h2">{section.title} <small>({section.checks.length})</small></h2>
          <ul className="jt-checks">
            {section.checks.map((check, index) => <CheckRow key={`${check.id}-${check.subject?.evidenceId ?? index}`} check={check} />)}
          </ul>
        </div>
      ))}

      {analysis.observations.length > 0 && (
        <div className="jt-section">
          <h2 className="jt-h2">What the services read</h2>
          <p className="jt-note">Readings are shown as returned. They are observations, not rule findings.</p>
          <div className="jt-obs-grid">
            {analysis.observations.map((obs) =>
              obs.kind === 'vision' ? <VisionCard key={obs.evidenceId} obs={obs} evidence={byId.get(obs.evidenceId)} />
                : obs.kind === 'textract' ? <SlipCard key={obs.evidenceId} obs={obs} evidence={byId.get(obs.evidenceId)} />
                  : obs.kind === 'trace' ? <TraceCard key={obs.evidenceId} obs={obs} />
                    : (
                      <figure key={obs.evidenceId} className="jt-obs text">
                        <figcaption><b>{obs.filename}</b><p>{obs.note}</p></figcaption>
                      </figure>
                    ),
            )}
          </div>
        </div>
      )}

      {analysis.evidenceExcluded.length > 0 && (
        <div className="jt-section">
          <h2 className="jt-h2">Files left out</h2>
          <ul className="jt-excluded">
            {analysis.evidenceExcluded.map((item) => (
              <li key={item.evidenceId}><b>{item.filename ?? item.evidenceId}</b> ({item.state.toLowerCase()}): {item.reason}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="jt-section jt-provenance">
        <h2 className="jt-h2">Provenance</h2>
        <dl className="jt-dl">
          <dt>Trial</dt><dd className="mono">{analysis.trialId}</dd>
          <dt>Analysis</dt><dd className="mono">{analysis.analysisId} (run {analysis.analysisCount})</dd>
          <dt>Photo model</dt><dd>{analysis.provenance.visionModelId ?? 'none (offline mock)'}</dd>
          <dt>Slip reader</dt><dd>{analysis.provenance.mockAws ? 'offline mock' : `Amazon Textract, ${analysis.provenance.textract}`}</dd>
          <dt>Rules</dt><dd>{analysis.provenance.rulesVersion}, reusing SiltProof R1–R10</dd>
        </dl>
        <ul className="jt-notes">{analysis.provenance.notes.map((note) => <li key={note}>{note}</li>)}</ul>
      </div>
    </section>
  )
}
