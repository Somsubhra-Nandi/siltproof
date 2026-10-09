import { useState } from 'react'
import type { ReactNode } from 'react'
import { ClaimForm, DisposalForm, DrainForm, TruckForm, WindowForm } from './DetailForms'
import type { Save } from './DetailForms'
import TrialMap from './TrialMap'
import type { PickMode } from './TrialMap'
import {
  READINESS_LABEL,
  STATE_LABEL,
  billReadiness,
  formatBytes,
  groupReadiness,
  hasPending,
  lineFromPhotos,
  photoPoints,
  traceRoutes,
} from './logic'
import type { Readiness } from './logic'
import type { Evidence, Group, LonLat, PhotoRole, Trial } from './types'

export type UploadProgress = {
  key: string
  group: Group
  name: string
  stage: 'registering' | 'uploading' | 'checking' | 'done' | 'error'
  progress: number
  error?: string
}

type Props = {
  trial: Trial
  uploads: UploadProgress[]
  onFiles: (group: Group, files: File[], role?: PhotoRole) => void
  onRetry: (evidence: Evidence) => void
  onDelete: (evidence: Evidence) => void
  save: Save
  onPick: (mode: Exclude<PickMode, null>, point: LonLat) => void
  onAnalyze: () => void
  analyzing: boolean
  analyzeError: string | null
}

const ACCEPT: Record<Group, string> = {
  photo: 'image/jpeg,image/png,.jpg,.jpeg,.png',
  slip: 'image/jpeg,image/png,application/pdf,.jpg,.jpeg,.png,.pdf',
  bill: 'application/pdf,image/jpeg,image/png,.pdf,.jpg,.jpeg,.png',
  trace: 'application/json,application/geo+json,.json,.geojson',
}

function Badge({ readiness }: { readiness: Readiness }) {
  return <span className={`jt-badge ${readiness}`}>{READINESS_LABEL[readiness]}</span>
}

function GroupCard({ number, title, readiness, children, intro }: {
  number: number; title: string; readiness: Readiness; children: ReactNode; intro: ReactNode
}) {
  return (
    <section className="jt-group" aria-labelledby={`jt-group-${number}`}>
      <header>
        <span className="jt-num">{number}</span>
        <h2 id={`jt-group-${number}`}>{title}</h2>
        <Badge readiness={readiness} />
      </header>
      <div className="jt-group-intro">{intro}</div>
      {children}
    </section>
  )
}

function Dropzone({ group, onFiles, role, label }: {
  group: Group; onFiles: Props['onFiles']; role?: PhotoRole; label: string
}) {
  const [over, setOver] = useState(false)
  return (
    <label
      className={`jt-drop${over ? ' is-over' : ''}`}
      onDragOver={(event) => { event.preventDefault(); setOver(true) }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => {
        event.preventDefault()
        setOver(false)
        onFiles(group, Array.from(event.dataTransfer.files), role)
      }}
    >
      <input
        type="file"
        multiple
        accept={ACCEPT[group]}
        onChange={(event) => {
          onFiles(group, Array.from(event.target.files ?? []), role)
          event.target.value = ''
        }}
      />
      <span>{label}</span>
    </label>
  )
}

function ResultLine({ item }: { item: Evidence }) {
  const result = item.result
  if (!result || item.state !== 'READY') return null
  if (item.group === 'photo') {
    const exif = result.exif
    const vision = result.vision
    return (
      <div className="jt-result">
        <span>
          {exif?.hasGps ? `GPS ${exif.lat?.toFixed(6)}, ${exif.lon?.toFixed(6)}` : 'No GPS in EXIF'}
          {' · '}
          {exif?.timestamp ? `${exif.timestamp.replace('T', ' ')}${exif.timestampHasOffset ? '' : ' (no timezone)'}` : 'No capture time'}
          {exif?.camera ? ` · ${exif.camera}` : ''}
        </span>
        {vision && (
          <span className={`jt-ai${vision.mocked ? ' mocked' : ''}`}>
            {vision.ran
              ? vision.mocked
                ? 'Offline mock, no model read this photo'
                : `Read by ${vision.modelId}${vision.input === 'processing_copy' ? ' (from a resized copy)' : ''}`
              : `Model not run: ${vision.skippedReason}`}
          </span>
        )}
      </div>
    )
  }
  if (item.group === 'slip') {
    const fields = result.fields ?? {}
    return (
      <div className="jt-result">
        <span>
          Net {fields.net?.value ?? '—'} t · {fields.vehicleNo?.value ?? 'vehicle unread'} · in {fields.timeIn?.value ?? '—'}
        </span>
        <span className={`jt-ai${result.mocked ? ' mocked' : ''}`}>
          {result.mocked ? 'Offline mock, not read by Textract' : `Textract, average confidence ${result.confidenceAvg}%`}
        </span>
      </div>
    )
  }
  if (item.group === 'trace') {
    return (
      <div className="jt-result">
        <span>
          {result.pointCount} points · {((result.distanceM ?? 0) / 1000).toFixed(2)} km · {result.vehicleNo ?? 'no vehicle number'}
        </span>
        {(result.warnings ?? []).map((warning) => <span key={warning} className="jt-warn">{warning}</span>)}
      </div>
    )
  }
  return <div className="jt-result"><span>Stored as supporting evidence. Bills are not machine-read.</span></div>
}

function EvidenceRow({ item, onRetry, onDelete }: { item: Evidence; onRetry: Props['onRetry']; onDelete: Props['onDelete'] }) {
  const busy = item.state === 'QUEUED' || item.state === 'PROCESSING' || item.state === 'UPLOADED'
  const problem = item.state === 'FAILED' || item.state === 'REJECTED' || Boolean(item.error)
  return (
    <li className={`jt-item state-${item.state.toLowerCase()}`}>
      <div className="jt-item-head">
        <span className="jt-item-name">
          {item.role && <b className="jt-role">{item.role}</b>}
          {item.filename}
        </span>
        <span className="jt-item-size">{formatBytes(item.sizeBytes ?? item.declaredSizeBytes)}</span>
        <span className={`jt-state${problem ? ' bad' : ''}`}>
          {busy && <span className="spinner" aria-hidden />}
          {STATE_LABEL[item.state]}
        </span>
      </div>
      <ResultLine item={item} />
      {item.error && <p className="jt-item-error">{item.error.message}</p>}
      <div className="jt-item-actions">
        {item.previewUrl && (
          <a className="link" href={item.previewUrl} target="_blank" rel="noreferrer">View original</a>
        )}
        {item.retryable && (
          <button type="button" className="link" onClick={() => onRetry(item)}>Retry processing</button>
        )}
        {!busy && (
          <button type="button" className="link danger" onClick={() => onDelete(item)}>Remove</button>
        )}
      </div>
    </li>
  )
}

function FileList({ trial, group, uploads, onRetry, onDelete }: {
  trial: Trial; group: Group; uploads: UploadProgress[]; onRetry: Props['onRetry']; onDelete: Props['onDelete']
}) {
  const items = trial.evidence.filter((item) => item.group === group && item.state !== 'UPLOADING')
  const live = uploads.filter((upload) => upload.group === group && upload.stage !== 'done')
  if (!items.length && !live.length) return null
  return (
    <ul className="jt-items">
      {live.map((upload) => (
        <li key={upload.key} className={`jt-item${upload.stage === 'error' ? ' state-failed' : ''}`}>
          <div className="jt-item-head">
            <span className="jt-item-name">{upload.name}</span>
            <span className="jt-state">
              {upload.stage === 'error' ? 'Not uploaded' : upload.stage === 'uploading' ? `Uploading ${Math.round(upload.progress * 100)}%` : upload.stage === 'checking' ? 'Checking file' : 'Preparing'}
            </span>
          </div>
          {upload.stage === 'uploading' && (
            <div className="jt-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(upload.progress * 100)} aria-label={`Upload of ${upload.name}`}>
              <span style={{ width: `${upload.progress * 100}%` }} />
            </div>
          )}
          {upload.error && <p className="jt-item-error">{upload.error}</p>}
        </li>
      ))}
      {items.map((item) => <EvidenceRow key={item.evidenceId} item={item} onRetry={onRetry} onDelete={onDelete} />)}
    </ul>
  )
}

export default function EvidenceScreen(props: Props) {
  const { trial, uploads, onFiles, onRetry, onDelete, save } = props
  const [pick, setPick] = useState<PickMode>(null)
  const [role, setRole] = useState<PhotoRole>('current')
  const details = trial.details
  const pending = hasPending(trial)
  const supplied = trial.evidence.some((item) => item.state === 'READY') || Object.keys(details).length > 0

  const list = (group: Group) => (
    <FileList trial={trial} group={group} uploads={uploads} onRetry={onRetry} onDelete={onDelete} />
  )

  return (
    <div className="jt-collect">
      <div className="jt-map-col">
        <TrialMap
          drain={details.drainLocation}
          disposal={details.disposalSite}
          photos={photoPoints(trial.evidence)}
          routes={traceRoutes(trial.evidence)}
          pickMode={pick}
          onPick={(mode, point) => {
            setPick(null)
            props.onPick(mode, point)
          }}
        />
      </div>

      <div className="jt-groups">
        <GroupCard number={1} title="Contractor bill" readiness={billReadiness(trial)}
          intro={<p>Type the claim; attach the bill as supporting evidence if you have it. SiltProof does not machine-read bills.</p>}>
          <ClaimForm key={JSON.stringify(details.claim ?? null)} value={details.claim} save={save} />
          <Dropzone group="bill" onFiles={onFiles} label="Attach the bill (PDF, JPEG or PNG, up to 10 MB)" />
          {list('bill')}
        </GroupCard>

        <GroupCard number={2} title="Drain photographs" readiness={groupReadiness(trial, 'photo')}
          intro={<p>Upload the original files from the phone (not via WhatsApp, which strips EXIF). Label each photo; SiltProof never assumes a photo is an “after” photo.</p>}>
          <div className="jt-row">
            <label className="jt-field">
              <span>Label for the next photos</span>
              <select value={role} onChange={(event) => setRole(event.target.value as PhotoRole)}>
                <option value="current">Current condition</option>
                <option value="before">Before</option>
                <option value="after">After</option>
                <option value="additional">Additional evidence</option>
              </select>
            </label>
          </div>
          <Dropzone group="photo" role={role} onFiles={onFiles} label="Add JPEG or PNG photos (up to 15 MB each)" />
          {list('photo')}
          <details className="jt-more">
            <summary>Work window, for the photo-date check</summary>
            <WindowForm key={JSON.stringify(details.workWindow ?? null)} value={details.workWindow} save={save} />
          </details>
        </GroupCard>

        <GroupCard number={3} title="Weighbridge slips" readiness={groupReadiness(trial, 'slip')}
          intro={<p>Each slip is read once by Amazon Textract. Multi-page PDFs are refused: upload one slip per page.</p>}>
          <Dropzone group="slip" onFiles={onFiles} label="Add slips (JPEG, PNG or one-page PDF, up to 10 MB)" />
          {list('slip')}
        </GroupCard>

        <GroupCard number={4} title="Truck GPS trace" readiness={groupReadiness(trial, 'trace')}
          intro={
            <p>
              JSON as <code>{'{"vehicleNo": …, "points": [{"lat", "lon", "t"}]}'}</code>, or a GeoJSON LineString
              Feature with <code>properties.times</code>. GPX and CSV are not supported.
            </p>
          }>
          <Dropzone group="trace" onFiles={onFiles} label="Add a trace (.json or .geojson, up to 2 MB)" />
          {list('trace')}
          <details className="jt-more">
            <summary>Truck details, for the capacity and vehicle checks</summary>
            <TruckForm key={JSON.stringify(details.truck ?? null)} value={details.truck} save={save} />
          </details>
        </GroupCard>

        <GroupCard number={5} title="Drain location" readiness={details.drainLocation ? 'ready' : 'missing'}
          intro={<p>Where the drain is, as you understand it. Shown on the map in blue with its tolerance.</p>}>
          <DrainForm
            key={JSON.stringify(details.drainLocation ?? null)}
            value={details.drainLocation}
            save={save}
            photoLine={lineFromPhotos(trial.evidence)}
            picking={pick === 'drain'}
            onPick={() => setPick(pick === 'drain' ? null : 'drain')}
          />
        </GroupCard>

        <GroupCard number={6} title="Designated disposal site" readiness={details.disposalSite ? 'ready' : 'missing'}
          intro={<p>Where the silt should have gone, for this trial. Shown in green.</p>}>
          <DisposalForm
            key={JSON.stringify(details.disposalSite ?? null)}
            value={details.disposalSite}
            save={save}
            picking={pick === 'disposal'}
            onPick={() => setPick(pick === 'disposal' ? null : 'disposal')}
          />
        </GroupCard>

        <div className="jt-analyze">
          <p>
            Analysis runs every check the evidence allows and marks the rest <b>not evaluated</b>.
            It calls no AI model; photos and slips were read once, when uploaded.
          </p>
          {props.analyzeError && <p className="jt-error" role="alert">{props.analyzeError}</p>}
          <button
            type="button"
            className={`btn btn-primary${props.analyzing ? ' is-busy' : ''}`}
            onClick={props.onAnalyze}
            disabled={props.analyzing || pending || !supplied}
          >
            {props.analyzing && <span className="spinner" aria-hidden />}
            {pending ? 'Waiting for files to finish processing…' : 'Run analysis'}
          </button>
        </div>
      </div>
    </div>
  )
}
