import { useState } from 'react'

type Props = {
  available: boolean
  busy: boolean
  error: string | null
  onCreate: (input: { label: string; inviteCode: string; kind: 'judge' | 'field' }) => void
}

const GROUPS = [
  ['Contractor bill', 'PDF, JPEG or PNG, plus the claimed quantity, rate and amount typed in. Bills are stored, not machine-read.'],
  ['Drain photographs', 'Original JPEGs keep their EXIF GPS and time; PNGs usually have none. Up to 15 MB each, 12 photos.'],
  ['Weighbridge slips', 'JPEG, PNG or a single-page PDF, read by Amazon Textract. Up to 10 MB each.'],
  ['Truck GPS trace', 'SiltProof trace JSON or a GeoJSON LineString with a timestamp per point. Up to 2 MB.'],
  ['Drain location', 'A point or line you type or click on the map, with a tolerance. Never treated as an official map.'],
  ['Disposal site', 'A point and radius, or a polygon, designated for this trial only.'],
]

export default function IntroScreen({ available, busy, error, onCreate }: Props) {
  const [label, setLabel] = useState('')
  const [inviteCode, setInviteCode] = useState('')
  const [fieldCase, setFieldCase] = useState(false)
  const [agreed, setAgreed] = useState(false)

  return (
    <section className="jt-intro" aria-labelledby="jt-intro-title">
      <p className="jt-eyebrow">Try SiltProof yourself</p>
      <h1 id="jt-intro-title" className="jt-title">Bring your own evidence</h1>
      <p className="jt-lede">
        Start a private trial, add whatever evidence you have, and SiltProof runs the checks it
        can. Anything it cannot check is reported as <strong>not evaluated</strong>, never as
        passed. Your trial is separate from the prepared 18-drain investigation, which it cannot
        change.
      </p>

      <h2 className="jt-h2">Six kinds of evidence, all optional</h2>
      <ol className="jt-groups-list">
        {GROUPS.map(([title, text], index) => (
          <li key={title}>
            <span className="jt-num">{index + 1}</span>
            <div>
              <b>{title}</b>
              <p>{text}</p>
            </div>
          </li>
        ))}
      </ol>

      <div className="jt-privacy" role="note">
        <h2 className="jt-h2">Before you upload</h2>
        <ul>
          <li>Files go to a private Amazon S3 bucket. Slips are read by Amazon Textract and photos by Amazon Bedrock (Amazon Nova Pro); records are kept in Amazon DynamoDB.</li>
          <li>Everything is set to be deleted about 48 hours after the trial starts. Deletion is asynchronous, so it can take longer; you can also delete the trial at any time.</li>
          <li>Photo EXIF can reveal exactly where and when a photo was taken.</li>
          <li>Do not upload confidential, personal or sensitive records.</li>
        </ul>
      </div>

      {!available ? (
        <p className="jt-callout warn" role="alert">
          Trials need the SiltProof API. This build has no <code>VITE_API_BASE_URL</code>, so it can
          only show the prepared investigation. Run <code>scripts/trial_dev_server.py</code> for an
          offline trial, or point the app at a deployed stack.
        </p>
      ) : (
        <form
          className="jt-create"
          onSubmit={(event) => {
            event.preventDefault()
            onCreate({ label, inviteCode, kind: fieldCase ? 'field' : 'judge' })
          }}
        >
          <label className="jt-field">
            <span>Trial name (optional)</span>
            <input value={label} maxLength={80} onChange={(event) => setLabel(event.target.value)} placeholder="e.g. Judge 3, ward drain" />
          </label>
          <label className="jt-field">
            <span>Invite code (if the team gave you one)</span>
            <input value={inviteCode} onChange={(event) => setInviteCode(event.target.value)} autoComplete="off" />
          </label>
          <label className="jt-check">
            <input type="checkbox" checked={fieldCase} onChange={(event) => setFieldCase(event.target.checked)} />
            <span>This is a field observation (current condition only, no bill)</span>
          </label>
          <label className="jt-check">
            <input type="checkbox" checked={agreed} onChange={(event) => setAgreed(event.target.checked)} />
            <span>I have read the notice above and will not upload sensitive records.</span>
          </label>
          {error && <p className="jt-error" role="alert">{error}</p>}
          <button type="submit" className={`btn btn-primary${busy ? ' is-busy' : ''}`} disabled={!agreed || busy}>
            {busy && <span className="spinner" aria-hidden />}
            Start a trial
          </button>
        </form>
      )}
    </section>
  )
}
