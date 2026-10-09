import { useEffect, useRef, useState } from 'react'

const COUNT_UP_MS = 900

/** "₹6.66 lakh" — the headline figure, spelled out. */
export function lakhLong(rupees: number) {
  return `₹${(rupees / 100000).toFixed(2)} lakh`
}

/** "₹6.66 L" — the small sub-figures, where space is tight. */
export function lakhShort(rupees: number) {
  return `₹${(rupees / 100000).toFixed(2)} L`
}

export function tonnes(value: number) {
  return `${Math.round(value).toLocaleString('en-IN')} t`
}

/**
 * Ease a number towards its target, so a verification lands as something that
 * happened rather than a jump cut. With `animate` false the target is
 * returned untouched and no state is involved at all.
 */
export function useCountUp(target: number, animate: boolean, duration = COUNT_UP_MS) {
  const [value, setValue] = useState(target)
  const fromRef = useRef(target)

  useEffect(() => {
    if (!animate) {
      fromRef.current = target
      return
    }

    const from = fromRef.current
    const started = performance.now()
    let frame = 0

    const step = (now: number) => {
      const progress = Math.min(1, (now - started) / duration)
      // easeOutCubic: quick off the mark, settles gently.
      const eased = 1 - Math.pow(1 - progress, 3)
      setValue(from + (target - from) * eased)

      if (progress < 1) {
        frame = requestAnimationFrame(step)
      } else {
        fromRef.current = target
      }
    }

    frame = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frame)
  }, [target, animate, duration])

  return animate ? value : target
}

const indian = new Intl.NumberFormat('en-IN')

/** "₹3,45,600": Indian digit grouping, whole rupees. */
export function rupees(value: number) {
  return `₹${indian.format(Math.round(value))}`
}

/** "6.66", the lakh figure without its unit. */
export function lakh(value: number, digits = 2) {
  return (value / 100000).toFixed(digits)
}

/** "1,240", grouped the Indian way. */
export function grouped(value: number, digits = 0) {
  return new Intl.NumberFormat('en-IN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value)
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "21 Sep to 4 Oct 2026" from the bill's work window. */
export function windowText([from, to]: [string, string]) {
  const day = (iso: string, year: boolean) => {
    const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
    return `${d} ${MONTHS[m - 1]}${year ? ` ${y}` : ''}`
  }
  return `${day(from, false)} to ${day(to, true)}`
}

/** "1 Oct, 15:11", in India time whatever the viewer's zone. */
export function dayTime(iso: string | null | undefined) {
  if (!iso) return 'no timestamp'
  // The data is written in IST; read it as written.
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}:\d{2})/.exec(iso)
  if (!match) return iso
  return `${Number(match[3])} ${MONTHS[Number(match[2]) - 1]}, ${match[4]}`
}
