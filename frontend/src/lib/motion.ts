import { useEffect, useState } from 'react'

const QUERY = '(prefers-reduced-motion: reduce)'

export function prefersReducedMotion() {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia(QUERY).matches
    : false
}

/** Tracks the reduced-motion preference, live. */
export function useReducedMotion() {
  const [reduced, setReduced] = useState(prefersReducedMotion)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const media = window.matchMedia(QUERY)
    const change = () => setReduced(media.matches)
    media.addEventListener?.('change', change)
    return () => media.removeEventListener?.('change', change)
  }, [])
  return reduced
}

export const ease = {
  out: (t: number) => 1 - Math.pow(1 - t, 3),
  inOut: (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2),
  linear: (t: number) => t,
}

/**
 * Run `onFrame` from 0 to 1 over `ms`. With reduced motion, or no time, it
 * jumps straight to the end state.
 */
export function tween(
  ms: number,
  onFrame: (progress: number) => void,
  curve: (t: number) => number = ease.out,
  reduced = prefersReducedMotion(),
): Promise<void> {
  return new Promise((resolve) => {
    if (reduced || ms <= 0) {
      onFrame(1)
      resolve()
      return
    }
    const started = performance.now()
    const step = (now: number) => {
      const t = Math.min(1, (now - started) / ms)
      onFrame(curve(t))
      if (t < 1) requestAnimationFrame(step)
      else resolve()
    }
    requestAnimationFrame(step)
  })
}

export function wait(ms: number, reduced = prefersReducedMotion()) {
  return new Promise<void>((resolve) => setTimeout(resolve, reduced ? 0 : ms))
}
