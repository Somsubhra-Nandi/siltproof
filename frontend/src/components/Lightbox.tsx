import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'
import { createPortal } from 'react-dom'

export interface LightboxItem {
  src: string
  alt: string
  caption?: ReactNode
  /** A row to outline on the image, as percentages of its height. */
  highlight?: { top: number; height: number } | null
}

interface Props {
  items: LightboxItem[]
  start?: number
  label: string
  onClose: () => void
}

/**
 * Evidence enlarged over the page: a modal dialog that keeps focus inside,
 * closes on Escape or the backdrop, and steps through several images with the
 * arrow keys.
 */
function Lightbox({ items, start = 0, label, onClose }: Props) {
  const [index, setIndex] = useState(Math.min(start, items.length - 1))
  const dialogRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const before = document.activeElement as HTMLElement | null
    closeRef.current?.focus()
    return () => before?.focus?.()
  }, [])

  const item = items[index]
  if (!item) return null
  const many = items.length > 1
  const step = (by: number) => setIndex((current) => (current + by + items.length) % items.length)

  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      onClose()
    } else if (many && event.key === 'ArrowRight') {
      event.preventDefault()
      step(1)
    } else if (many && event.key === 'ArrowLeft') {
      event.preventDefault()
      step(-1)
    } else if (event.key === 'Tab') {
      // Keep focus on the dialog's own buttons.
      const buttons = [...(dialogRef.current?.querySelectorAll<HTMLButtonElement>('button') ?? [])]
      const at = buttons.indexOf(document.activeElement as HTMLButtonElement)
      event.preventDefault()
      buttons[(at + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length]?.focus()
    }
  }

  return createPortal(
    <div className="lightbox" onClick={(event) => event.target === event.currentTarget && onClose()}>
      <div
        ref={dialogRef}
        className="lightbox-panel"
        role="dialog"
        aria-modal="true"
        aria-label={label}
        onKeyDown={onKey}
      >
        <div className="lightbox-bar">
          <span>
            {label}
            {many && <span className="lightbox-count num"> · {index + 1} of {items.length}</span>}
          </span>
          <button ref={closeRef} type="button" className="lightbox-close" onClick={onClose} aria-label="Close">
            <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
              <path d="M3 3l12 12M15 3 3 15" stroke="currentColor" strokeWidth="2" />
            </svg>
          </button>
        </div>
        <figure className="lightbox-figure">
          <span className="lightbox-img">
            <img src={item.src} alt={item.alt} />
            {item.highlight && (
              <span
                className="hl"
                style={{ top: `${item.highlight.top}%`, height: `${item.highlight.height}%` }}
                aria-hidden="true"
              />
            )}
          </span>
          {item.caption && <figcaption>{item.caption}</figcaption>}
        </figure>
        {many && (
          <div className="lightbox-nav">
            <button type="button" className="btn-step" onClick={() => step(-1)} aria-label="Previous image">
              ‹ Previous
            </button>
            <button type="button" className="btn-step" onClick={() => step(1)} aria-label="Next image">
              Next ›
            </button>
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}

export default Lightbox
