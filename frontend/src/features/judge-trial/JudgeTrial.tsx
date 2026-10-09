// "Try SiltProof yourself": the root of the judge-trial feature.
//
// Self-contained on purpose: it owns its state, its API client and its
// styles, so the main app only has to link to it (docs/JUDGE-TRIAL-HANDOFF.md).
// It never reads or writes Bill B1.

import { useCallback, useEffect, useRef, useState } from 'react'
import * as api from './api'
import { TrialApiError } from './api'
import type { SaveResult } from './DetailForms'
import EvidenceScreen from './EvidenceScreen'
import type { UploadProgress } from './EvidenceScreen'
import IntroScreen from './IntroScreen'
import ResultsScreen from './ResultsScreen'
import { checkFile, contentTypeFor, hasPending, hoursLeft } from './logic'
import type { Analysis, Evidence, Group, LonLat, PhotoRole, Session, Trial } from './types'
import './trial.css'

export const POLL_MS = 2500

type Props = {
  /** Where "Explore the investigation" goes. */
  investigationHref?: string
}

function message(error: unknown): string {
  if (error instanceof TrialApiError) return error.message
  return error instanceof Error ? error.message : String(error)
}

export default function JudgeTrial({ investigationHref = '/' }: Props) {
  const [session, setSession] = useState<Session | null>(() => api.loadSession())
  const [trial, setTrial] = useState<Trial | null>(null)
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [view, setView] = useState<'collect' | 'results'>('collect')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [uploads, setUploads] = useState<UploadProgress[]>([])
  const [analyzing, setAnalyzing] = useState(false)
  const [analyzeError, setAnalyzeError] = useState<string | null>(null)
  const [resetOpen, setResetOpen] = useState(false)
  const [resetBusy, setResetBusy] = useState(false)
  const uploadSeq = useRef(0)
  const available = api.trialsAvailable()

  const forget = useCallback((why: string | null) => {
    api.saveSession(null)
    setSession(null)
    setTrial(null)
    setAnalysis(null)
    setUploads([])
    setView('collect')
    setLoadError(why)
  }, [])

  const refresh = useCallback(async (current: Session) => {
    try {
      const fresh = await api.getTrial(current)
      setTrial(fresh)
      setLoadError(null)
      return fresh
    } catch (error) {
      if (error instanceof TrialApiError && (error.status === 404 || error.status === 410)) {
        forget(error.status === 410 ? 'That trial has expired.' : 'That trial no longer exists.')
      } else {
        setLoadError(message(error))
      }
      return null
    }
  }, [forget])

  // Restore a trial after a reload, with its last analysis if it has one.
  useEffect(() => {
    if (!session || !available) return
    let cancelled = false
    api.getTrial(session).then(
      (fresh) => {
        if (cancelled) return
        setTrial(fresh)
        if (fresh.hasResults) {
          api.getResults(session).then((result) => !cancelled && setAnalysis(result), () => undefined)
        }
      },
      (error) => {
        if (cancelled) return
        if (error instanceof TrialApiError && (error.status === 404 || error.status === 410)) {
          forget(error.status === 410 ? 'That trial has expired.' : 'That trial no longer exists.')
        } else setLoadError(message(error))
      },
    )
    return () => {
      cancelled = true
    }
    // Only when the trial changes, not on every refresh.
  }, [session, available, forget])

  // Poll while anything is queued or processing.
  const pending = hasPending(trial)
  useEffect(() => {
    if (!session || !pending) return
    const timer = window.setInterval(() => {
      void refresh(session)
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [session, pending, refresh])

  async function create(input: { label: string; inviteCode: string; kind: 'judge' | 'field' }) {
    setCreating(true)
    setCreateError(null)
    try {
      const created = await api.createTrial({
        label: input.label.trim() || undefined,
        kind: input.kind,
        inviteCode: input.inviteCode.trim() || undefined,
      })
      const next = { trialId: created.trialId, token: created.accessToken }
      api.saveSession(next)
      setLoadError(null)
      setSession(next)
    } catch (error) {
      setCreateError(message(error))
    } finally {
      setCreating(false)
    }
  }

  function track(key: string, patch: Partial<UploadProgress>) {
    setUploads((list) => list.map((item) => (item.key === key ? { ...item, ...patch } : item)))
  }

  async function uploadOne(current: Session, file: File, group: Group, role?: PhotoRole) {
    const key = `u${(uploadSeq.current += 1)}`
    setUploads((list) => [...list, { key, group, name: file.name, stage: 'registering', progress: 0 }])
    try {
      const ticket = await api.requestUpload(current, {
        group,
        role: group === 'photo' ? role : undefined,
        filename: file.name,
        contentType: contentTypeFor(file),
        sizeBytes: file.size,
      })
      track(key, { stage: 'uploading' })
      await api.uploadToStorage(ticket, file, (fraction) => track(key, { progress: fraction }))
      track(key, { stage: 'checking', progress: 1 })
      try {
        await api.completeUpload(current, ticket.evidenceId)
        track(key, { stage: 'done' })
      } catch (error) {
        // A rejected file is recorded on the trial with its reason; show that.
        if (error instanceof TrialApiError && error.status === 422) track(key, { stage: 'done' })
        else throw error
      }
    } catch (error) {
      track(key, { stage: 'error', error: message(error) })
    }
  }

  async function handleFiles(group: Group, files: File[], role?: PhotoRole) {
    if (!session || !trial) return
    let known: Evidence[] = trial.evidence
    for (const file of files) {
      const problem = checkFile(file, group, trial.limits, known)
      if (problem) {
        const key = `u${(uploadSeq.current += 1)}`
        setUploads((list) => [...list, { key, group, name: file.name, stage: 'error', progress: 0, error: problem }])
        continue
      }
      await uploadOne(session, file, group, role)
      known = (await refresh(session))?.evidence ?? known
    }
  }

  async function save(body: Record<string, unknown>): Promise<SaveResult> {
    if (!session) return { ok: false, fields: {}, message: 'No trial.' }
    try {
      await api.putDetails(session, body)
      await refresh(session)
      return { ok: true }
    } catch (error) {
      const fields = error instanceof TrialApiError ? ((error.body.fields as Record<string, string>) ?? {}) : {}
      return { ok: false, fields, message: message(error) }
    }
  }

  async function pick(mode: 'drain' | 'disposal', point: LonLat) {
    if (!trial) return
    if (mode === 'drain') {
      const current = trial.details.drainLocation
      await save({
        drainLocation: {
          toleranceM: current?.toleranceM ?? 30,
          name: current?.name ?? null,
          lengthM: current?.lengthM,
          widthM: current?.widthM,
          depthM: current?.depthM,
          point,
          source: 'map_selected',
          derivedFromPhotos: false,
        },
      })
    } else {
      const current = trial.details.disposalSite
      await save({ disposalSite: { radiusM: current?.radiusM ?? 150, name: current?.name ?? null, point } })
    }
  }

  async function runAnalysis() {
    if (!session) return
    setAnalyzing(true)
    setAnalyzeError(null)
    try {
      const result = await api.analyzeTrial(session)
      setAnalysis(result)
      setView('results')
      await refresh(session)
    } catch (error) {
      setAnalyzeError(message(error))
    } finally {
      setAnalyzing(false)
    }
  }

  async function retry(item: Evidence) {
    if (!session) return
    try {
      await api.retryEvidence(session, item.evidenceId)
    } catch (error) {
      setLoadError(message(error))
    }
    await refresh(session)
  }

  async function remove(item: Evidence) {
    if (!session) return
    try {
      await api.deleteEvidence(session, item.evidenceId)
    } catch (error) {
      setLoadError(message(error))
    }
    await refresh(session)
  }

  async function startOver(deleteCurrent: boolean) {
    if (deleteCurrent && session) {
      setResetBusy(true)
      try {
        await api.deleteTrial(session)
      } catch (error) {
        setResetBusy(false)
        setLoadError(`The trial could not be deleted: ${message(error)}`)
        return
      }
      setResetBusy(false)
    }
    setResetOpen(false)
    forget(null)
  }

  return (
    <div className="jt">
      <header className="jt-masthead">
        <a className="wordmark" href={investigationHref}>Silt<i>Proof</i></a>
        <span className="jt-mode">Try it yourself</span>
        <span className="jt-real-badge">Your evidence, not simulated</span>
        <nav className="jt-nav">
          <a className="link" href={investigationHref}>Explore the investigation</a>
          {trial && (
            <button type="button" className="btn btn-quiet" onClick={() => setResetOpen(true)}>
              Start another trial
            </button>
          )}
        </nav>
      </header>

      {trial && (
        <p className="jt-trialbar">
          Trial <span className="mono">{trial.trialId}</span>
          {trial.label ? ` · ${trial.label}` : ''}
          {trial.kind === 'field' ? ' · field observation' : ''}
          {' · '}deleted in about {hoursLeft(trial.expiresAt)} h
          {trial.mockAws && <b className="jt-mock-tag"> · OFFLINE MOCK SERVICES</b>}
        </p>
      )}

      {resetOpen && trial && (
        <div className="jt-reset" role="dialog" aria-modal="false" aria-labelledby="jt-reset-title">
          <h2 id="jt-reset-title" className="jt-h2">Start another trial</h2>
          <p>The new trial starts empty. Nothing carries over, and the prepared investigation is never affected.</p>
          <div className="jt-row">
            <button type="button" className={`btn btn-held${resetBusy ? ' is-busy' : ''}`} disabled={resetBusy} onClick={() => startOver(true)}>
              {resetBusy && <span className="spinner" aria-hidden />}Delete this trial now, then start
            </button>
            <button type="button" className="btn btn-quiet" disabled={resetBusy} onClick={() => startOver(false)}>
              Keep it (it expires on its own) and start
            </button>
            <button type="button" className="link" onClick={() => setResetOpen(false)}>Cancel</button>
          </div>
        </div>
      )}

      {loadError && <p className="jt-callout err" role="alert">{loadError}</p>}

      <main className="jt-main">
        {!session || !available ? (
          <IntroScreen available={available} busy={creating} error={createError} onCreate={create} />
        ) : !trial ? (
          <div className="jt-loading" aria-busy="true"><div className="skeleton" /><div className="skeleton" /></div>
        ) : view === 'results' && analysis ? (
          <ResultsScreen trial={trial} analysis={analysis} analyzing={analyzing} onBack={() => setView('collect')} onReanalyze={runAnalysis} />
        ) : (
          <>
            {analysis && (
              <p className="jt-trialbar">
                <button type="button" className="link" onClick={() => setView('results')}>Show the last results</button>
              </p>
            )}
            <EvidenceScreen
              trial={trial}
              uploads={uploads}
              onFiles={handleFiles}
              onRetry={retry}
              onDelete={remove}
              save={save}
              onPick={pick}
              onAnalyze={runAnalysis}
              analyzing={analyzing}
              analyzeError={analyzeError}
            />
          </>
        )}
      </main>
    </div>
  )
}
