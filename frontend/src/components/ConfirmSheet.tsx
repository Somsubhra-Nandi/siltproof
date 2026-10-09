import { useEffect, useId, useRef, useState } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'
import { offline } from '../api'

export interface Move {
  label: string
  value: string
}

interface Props {
  title: string
  children: ReactNode
  moves: Move[]
  kind: 'APPROVE' | 'HOLD'
  confirmLabel: string
  /** When set, a note of at least this many characters is required. */
  requireNote?: { label: string; min: number } | null
  busy: boolean
  error: string | null
  floating?: boolean
  onCancel: () => void
  onConfirm: (note: string | null) => void
}

/**
 * The confirmation before a decision is saved: the question, its
 * consequence, and what the money does. Focus moves in on open, Escape
 * cancels, Tab stays inside, and focus goes back to the opener on close.
 */
function ConfirmSheet({
  title,
  children,
  moves,
  kind,
  confirmLabel,
  requireNote = null,
  busy,
  error,
  floating = false,
  onCancel,
  onConfirm,
}: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const headingId = useId()
  const noteId = useId()
  const [note, setNote] = useState('')

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    const first = ref.current?.querySelector<HTMLElement>('textarea, [data-confirm]')
    first?.focus({ preventScroll: true })
    ref.current?.scrollIntoView?.({ block: 'nearest' })
    return () => {
      if (opener && document.contains(opener)) opener.focus({ preventScroll: true })
    }
  }, [])

  const noteShort = requireNote ? note.trim().length < requireNote.min : false

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape' && !busy) {
      event.stopPropagation()
      onCancel()
      return
    }
    if (event.key !== 'Tab' || !ref.current) return
    const focusable = [
      ...ref.current.querySelectorAll<HTMLElement>('button:not([disabled]), textarea:not([disabled])'),
    ]
    if (!focusable.length) return
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  return (
    <div
      ref={ref}
      className={`confirm ${floating ? 'floating' : ''}`}
      role="dialog"
      aria-modal="true"
      aria-labelledby={headingId}
      onKeyDown={onKeyDown}
    >
      <h4 id={headingId}>{title}</h4>
      <div className="confirm-body">{children}</div>
      <dl className="moves">
        {moves.map((move) => (
          <div key={move.label}>
            <dt>{move.label}</dt>
            <dd className="mono">{move.value}</dd>
          </div>
        ))}
      </dl>
      {requireNote && (
        <>
          <label htmlFor={noteId}>{requireNote.label}</label>
          <textarea
            id={noteId}
            value={note}
            disabled={busy}
            onChange={(event) => setNote(event.target.value)}
            placeholder="What you inspected, when, and what you found"
          />
          {noteShort && (
            <p className="need">
              At least {requireNote.min} characters; {note.trim().length} so far.
            </p>
          )}
        </>
      )}
      {error && (
        <div className="err" role="alert">
          <b>Not saved.</b> {error} Nothing changed on the bill; try again.
        </div>
      )}
      <div className="two">
        <button type="button" className="btn btn-quiet" onClick={onCancel} disabled={busy}>
          Keep reviewing
        </button>
        <button
          type="button"
          data-confirm
          className={`btn ${kind === 'APPROVE' ? 'btn-primary' : 'btn-held'} ${busy ? 'is-busy' : ''}`}
          disabled={busy || noteShort}
          aria-busy={busy}
          onClick={() => onConfirm(requireNote ? note.trim() : null)}
        >
          {busy ? (
            <>
              <span className="spinner" aria-hidden="true" />
              {offline ? 'Applying decision' : 'Saving decision'}
            </>
          ) : (
            confirmLabel
          )}
        </button>
      </div>
    </div>
  )
}

export default ConfirmSheet
