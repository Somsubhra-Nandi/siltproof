import { useState } from 'react'
import type { KeyboardEvent, PointerEvent } from 'react'

import type { CaseFacts } from '../lib/caseFacts'
import { allFindings, photoTitle } from '../lib/caseFacts'
import { PHASH_DUPLICATE_MAX } from '../lib/timeline'
import { dayTime } from '../format'
import type { Drain, Finding, Photo } from '../types'

const PHOTO_RULES = ['R1', 'R2', 'R3', 'R4', 'PHOTOS_MISSING']

interface Props {
  drain: Drain
  facts: CaseFacts
  /** The earlier photo an R3 finding says was copied, from the other drain. */
  original: Photo | null
  /** ?photos=slot: show the reserved frames for the real photos instead. */
  forceSlot: boolean
  expired: boolean
  onImageError: () => void
}

const fileOf = (key: string) => key.split('/').slice(-2).join('/')

function Bits({ hash, label }: { hash: string; label: string }) {
  const bits = [...hash].flatMap((h) => [...parseInt(h, 16).toString(2).padStart(4, '0')])
  return (
    <div className="bits" title={hash} role="img" aria-label={label}>
      {bits.map((bit, index) => (
        <i key={index} className={bit === '1' ? 'on' : ''} />
      ))}
    </div>
  )
}

function Slot({ who, expired }: { who: string; expired: boolean }) {
  return (
    <div className="photoslot">
      <svg width="40" height="32" viewBox="0 0 40 32" aria-hidden="true">
        <rect x="1" y="5" width="38" height="26" rx="2" fill="none" stroke="currentColor" strokeWidth="2" />
        <circle cx="20" cy="18" r="7" fill="none" stroke="currentColor" strokeWidth="2" />
        <rect x="13" y="1" width="14" height="6" fill="currentColor" />
      </svg>
      <b>{who}</b>
      <span>
        {expired
          ? 'Photo link expired. Reopen the drain to fetch a new one.'
          : 'Real GPS-tagged photo goes here. Not yet supplied.'}
      </span>
    </div>
  )
}

/** The model's reading of a photo: never styled like a rule finding. */
function AiObservation({ photo }: { photo: Photo }) {
  const ai = photo.bedrock
  if (!ai?.notes) return null
  const mocked = ai.mocked || !ai.modelId
  return (
    <div className="ai">
      <div className="k">
        <span>AI observation, not a finding</span>
        <span>Bedrock vision</span>
      </div>
      <p>“{ai.notes}”</p>
      <small>
        {mocked
          ? 'Offline: a canned sample, no model was called.'
          : `Read by ${ai.modelId} when the photo was uploaded.`}{' '}
        It describes what a photo shows; it cannot tell that a photo was filed twice. The rules can.
      </small>
    </div>
  )
}

function FindingBox({ finding }: { finding: Finding }) {
  const usesModel = finding.rule === 'R4'
  return (
    <div className={`finding ${finding.severity}`}>
      <div className="k">
        <span>
          Rule {finding.rule}, deterministic{usesModel ? ', applied to the Bedrock reading' : ''}
        </span>
        <span>{finding.severity === 'hard' ? 'hard fail' : 'soft fail'}</span>
      </div>
      <p>{finding.message}</p>
    </div>
  )
}

function Compare({ left, right, leftLabel, rightLabel, onImageError }: {
  left: string
  right: string
  leftLabel: string
  rightLabel: string
  onImageError: () => void
}) {
  const [cut, setCut] = useState(50)
  const [dragging, setDragging] = useState(false)
  const set = (value: number) => setCut(Math.max(0, Math.min(100, value)))
  const fromPointer = (event: PointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    set(((event.clientX - rect.left) / rect.width) * 100)
  }
  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'ArrowLeft') { event.preventDefault(); set(cut - 5) }
    if (event.key === 'ArrowRight') { event.preventDefault(); set(cut + 5) }
    if (event.key === 'Home') { event.preventDefault(); set(0) }
    if (event.key === 'End') { event.preventDefault(); set(100) }
  }
  return (
    <div
      className="compare"
      tabIndex={0}
      role="slider"
      aria-label={`Compare ${leftLabel} with ${rightLabel}`}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(cut)}
      style={{ ['--cut' as string]: `${cut}%` }}
      onPointerDown={(event) => {
        setDragging(true)
        event.currentTarget.setPointerCapture?.(event.pointerId)
        fromPointer(event)
      }}
      onPointerMove={(event) => dragging && fromPointer(event)}
      onPointerUp={() => setDragging(false)}
      onKeyDown={onKey}
    >
      <img src={left} alt={leftLabel} onError={onImageError} draggable={false} />
      <img className="top" src={right} alt={rightLabel} onError={onImageError} draggable={false} />
      <span className="side l">{leftLabel}</span>
      <span className="side r">{rightLabel}</span>
      <span className="handle" />
      <span className="knob" aria-hidden="true">
        <svg width="18" height="12" viewBox="0 0 18 12">
          <path d="M5 1 1 6l4 5M13 1l4 5-4 5" fill="none" stroke="#1C2A38" strokeWidth="1.8" />
        </svg>
      </span>
      <span className="tag-sim">Simulated photo</span>
    </div>
  )
}

function ExhibitPhotos({ drain, facts, original, forceSlot, expired, onImageError }: Props) {
  const findings = allFindings(drain).filter((f) => PHOTO_RULES.includes(f.rule))
  const r3 = facts.r3
  const rules = [...new Set(findings.map((f) => f.rule))]

  let body
  if (r3) {
    const copyKey = String(r3.evidence.s3Key ?? '')
    const origKey = String(r3.evidence.original ?? '')
    const copy = drain.photos.find((photo) => photo.s3Key === copyKey) ?? null
    const originalDrain = String(r3.evidence.originalDrainId ?? '?')
    const leftLabel = `Drain ${originalDrain}, filed first`
    const rightLabel = `Drain ${drain.drainId} copy`
    const canShow = !forceSlot && !expired && original?.imageUrl && copy?.imageUrl
    body = (
      <div className="photogrid">
        <div>
          {canShow ? (
            <Compare
              left={original!.imageUrl!}
              right={copy!.imageUrl!}
              leftLabel={leftLabel}
              rightLabel={rightLabel}
              onImageError={onImageError}
            />
          ) : (
            <div className="slotpair">
              <Slot who={leftLabel} expired={expired} />
              <Slot who={rightLabel} expired={expired} />
            </div>
          )}
          <div className="cmp-cap">
            <span>
              <b>{fileOf(origKey)}</b>
              {original ? `${dayTime(original.timestamp)}, inside drain ${originalDrain}` : `filed for drain ${originalDrain}`}
            </span>
            <span>
              <b>{fileOf(copyKey)}</b>
              {copy ? `${dayTime(copy.timestamp)}, billed for drain ${drain.drainId}` : ''}
            </span>
          </div>
        </div>
        <div>
          {original?.pHash && copy?.pHash && (
            <div className="hashes">
              <Bits hash={original.pHash} label={`Perceptual hash of the drain ${originalDrain} photo`} />
              <Bits hash={copy.pHash} label={`Perceptual hash of the drain ${drain.drainId} photo`} />
              <p>
                Perceptual hashes differ in <b className="mono">{String(r3.evidence.hammingDistance)} of 64</b> bits.
                R3 calls {PHASH_DUPLICATE_MAX} or fewer a copy.
              </p>
            </div>
          )}
          <FindingBox finding={r3} />
          {copy && <AiObservation photo={copy} />}
        </div>
      </div>
    )
  } else {
    // Show the photo a finding names, or the after-photos, up to three.
    const named = new Set(findings.map((f) => String(f.evidence.s3Key ?? '')))
    const ordered = [
      ...drain.photos.filter((photo) => named.has(photo.s3Key)),
      ...drain.photos.filter((photo) => !named.has(photo.s3Key) && photo.role === 'after'),
      ...drain.photos.filter((photo) => !named.has(photo.s3Key) && photo.role !== 'after'),
    ].slice(0, 2)
    const focus = ordered[0] ?? null
    body = drain.photos.length === 0 ? (
      <div className="warn">No photographs were filed for this drain.</div>
    ) : (
      <div className="photogrid">
        <div>
          <div className="slotpair">
            {ordered.map((photo) =>
              photo.imageUrl && !forceSlot && !expired ? (
                <figure key={photo.s3Key} className="photo-frame">
                  <img src={photo.imageUrl} alt={`${photo.role ?? 'drain'} photo ${fileOf(photo.s3Key)}`} onError={onImageError} />
                  <span className="tag-sim">Simulated photo</span>
                </figure>
              ) : (
                <Slot key={photo.s3Key} who={fileOf(photo.s3Key)} expired={expired} />
              ),
            )}
          </div>
          <div className="cmp-cap">
            {ordered.map((photo) => (
              <span key={photo.s3Key}>
                <b>{fileOf(photo.s3Key)}</b>
                {photo.role ?? 'photo'}, {dayTime(photo.timestamp)}
                {photo.hasGps ? '' : ', no GPS'}
              </span>
            ))}
          </div>
        </div>
        <div>
          {findings.length ? (
            findings.slice(0, 2).map((f) => <FindingBox key={f.rule + f.message} finding={f} />)
          ) : (
            <p className="pass-note">
              {drain.verdict ? 'Every photo check passed: R1 to R4.' : 'Not checked yet.'}
            </p>
          )}
          {focus && <AiObservation photo={focus} />}
        </div>
      </div>
    )
  }

  return (
    <article className="exhibit" data-shot="photos" aria-labelledby="exhibit-c">
      <div className="ex-head">
        <span className="ex-tag">Exhibit C</span>
        <h2 id="exhibit-c">{photoTitle(facts)}</h2>
        <span className="rules">
          {rules.map((rule) => (
            <span key={rule} className={`rid ${findings.find((f) => f.rule === rule)?.severity === 'soft' ? 'soft' : ''}`}>
              {rule}
            </span>
          ))}
        </span>
      </div>
      {body}
    </article>
  )
}

export default ExhibitPhotos
