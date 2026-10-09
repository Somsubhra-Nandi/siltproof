// The judge-trial client. Every call goes to the trial API on the same
// VITE_API_BASE_URL as the rest of the app; there is no offline fake, because
// a trial is only meaningful when real processing runs (docs/JUDGE-TRIAL-API.md).

import type {
  Analysis,
  CreatedTrial,
  Details,
  Evidence,
  Group,
  PhotoRole,
  Session,
  Trial,
  UploadTicket,
} from './types'

export class TrialApiError extends Error {
  status: number
  code: string
  body: Record<string, unknown>

  constructor(status: number, code: string, message: string, body: Record<string, unknown>) {
    super(message)
    this.status = status
    this.code = code
    this.body = body
  }
}

export function apiBase(): string {
  return (import.meta.env.VITE_API_BASE_URL ?? '').trim().replace(/\/$/, '')
}

export const trialsAvailable = () => apiBase() !== ''

async function request<T>(path: string, init: RequestInit & { token?: string } = {}): Promise<T> {
  const { token, ...rest } = init
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`

  let response: Response
  try {
    response = await fetch(`${apiBase()}${path}`, { ...rest, headers: { ...headers, ...(rest.headers as object) } })
  } catch {
    throw new TrialApiError(0, 'NETWORK', 'The SiltProof API could not be reached.', {})
  }
  const text = await response.text()
  let payload: Record<string, unknown>
  try {
    payload = text ? JSON.parse(text) : {}
  } catch {
    payload = { error: text }
  }
  if (!response.ok) {
    throw new TrialApiError(
      response.status,
      String(payload.code ?? 'HTTP_' + response.status),
      String(payload.error ?? `${response.status} ${response.statusText}`),
      payload,
    )
  }
  return payload as T
}

export function createTrial(body: { label?: string; kind?: 'judge' | 'field'; inviteCode?: string }) {
  return request<CreatedTrial>('/trials', { method: 'POST', body: JSON.stringify(body) })
}

export function getTrial(session: Session) {
  return request<Trial>(`/trials/${session.trialId}`, { token: session.token })
}

export function putDetails(session: Session, body: Partial<Record<keyof Details, unknown>>) {
  return request<{ trialId: string; details: Details }>(`/trials/${session.trialId}/details`, {
    method: 'PUT',
    token: session.token,
    body: JSON.stringify(body),
  })
}

export function requestUpload(
  session: Session,
  body: { group: Group; role?: PhotoRole; filename: string; contentType: string; sizeBytes: number },
) {
  return request<UploadTicket>(`/trials/${session.trialId}/upload-url`, {
    method: 'POST',
    token: session.token,
    body: JSON.stringify(body),
  })
}

/**
 * POST the file straight to S3 with the presigned policy. XHR rather than
 * fetch, because fetch cannot report upload progress.
 */
export function uploadToStorage(
  ticket: UploadTicket,
  file: Blob,
  onProgress: (fraction: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const form = new FormData()
    for (const [name, value] of Object.entries(ticket.upload.fields)) form.append(name, value)
    form.append('file', file) // S3 requires the file to be the last field.

    const xhr = new XMLHttpRequest()
    xhr.open('POST', ticket.upload.url)
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total)
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve()
      else
        reject(
          new TrialApiError(xhr.status, 'STORAGE_REFUSED', 'Storage refused the upload (it may have expired or be too large).', {}),
        )
    }
    xhr.onerror = () => reject(new TrialApiError(0, 'NETWORK', 'The upload to storage failed.', {}))
    xhr.send(form)
  })
}

export function completeUpload(session: Session, evidenceId: string) {
  return request<{ evidence: Evidence; processing?: string }>(
    `/trials/${session.trialId}/evidence/${evidenceId}/complete`,
    { method: 'POST', token: session.token, body: '{}' },
  )
}

export function retryEvidence(session: Session, evidenceId: string) {
  return request<{ evidence: Evidence }>(`/trials/${session.trialId}/evidence/${evidenceId}/retry`, {
    method: 'POST',
    token: session.token,
    body: '{}',
  })
}

export function deleteEvidence(session: Session, evidenceId: string) {
  return request<{ deleted: string }>(`/trials/${session.trialId}/evidence/${evidenceId}`, {
    method: 'DELETE',
    token: session.token,
  })
}

export function analyzeTrial(session: Session) {
  return request<Analysis>(`/trials/${session.trialId}/analyze`, {
    method: 'POST',
    token: session.token,
    body: '{}',
  })
}

export function getResults(session: Session) {
  return request<Analysis>(`/trials/${session.trialId}/results`, { token: session.token })
}

export function deleteTrial(session: Session) {
  return request<{ deleted: string }>(`/trials/${session.trialId}`, { method: 'DELETE', token: session.token })
}

// ------------------------------------------------------------ session store
// The token lives in this tab only: sessionStorage survives a reload, not a
// closed tab. Storage can be unavailable (private windows), so every access
// is guarded and the feature still works without it.
const KEY = 'siltproof.judgeTrial'

export function loadSession(): Session | null {
  try {
    const raw = window.sessionStorage.getItem(KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Session
    return parsed?.trialId && parsed?.token ? parsed : null
  } catch {
    return null
  }
}

export function saveSession(session: Session | null) {
  try {
    if (session) window.sessionStorage.setItem(KEY, JSON.stringify(session))
    else window.sessionStorage.removeItem(KEY)
  } catch {
    // Not fatal: the trial simply will not survive a reload.
  }
}
