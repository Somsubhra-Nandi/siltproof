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
