import { useLayoutEffect, useRef, useState } from 'react'

import { clock, placeLabels } from '../lib/timeline'
import type { Timeline } from '../lib/timeline'
import { minutesOf } from '../lib/caseFacts'

const NAME_W = 168
const END_W = 150

let canvas: HTMLCanvasElement | null = null

/** Label width in px, measured in the fonts the label is set in. */
function measure(time: string, text: string) {
  try {
    canvas ??= document.createElement('canvas')
    const ctx = canvas.getContext('2d')
    if (ctx) {
      ctx.font = "600 15px 'IBM Plex Mono', ui-monospace, monospace"
      const a = ctx.measureText(time).width
      ctx.font = "400 15px 'Instrument Sans', system-ui, sans-serif"
      return a + 6 + ctx.measureText(text).width
    }
  } catch {
    // jsdom has no canvas; fall through to an estimate.
  }
  return (time.length + text.length) * 8.5 + 6
}

function CaseTimeline({ timeline }: { timeline: Timeline }) {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  const [fontsReady, setFontsReady] = useState(false)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    // Before layout (and in jsdom) there is no width; assume the 1920 track.
    // On a phone the timeline keeps a readable minimum width (--tl-min, set in
    // CSS) and scrolls sideways, rather than squeezing its labels together.
    const update = () => {
      const min = parseFloat(getComputedStyle(el).getPropertyValue('--tl-min')) || 0
      setWidth(el.clientWidth ? Math.max(0, Math.max(el.clientWidth, min) - NAME_W - END_W) : 720)
    }
    update()
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(update) : null
    observer?.observe(el)
    // Re-measure once the web fonts are in, since the labels are measured.
    document.fonts?.ready.then(() => setFontsReady(true))
    return () => observer?.disconnect()
  }, [])

  const { lo, hi } = timeline
  const x = (time: string) => ((minutesOf(time) - lo) / (hi - lo)) * width
  void fontsReady

  return (
    <div className="timeline" ref={ref} aria-label="Timeline of the trip">
      {width > 0 && (
        <>
          <div className="tl-axis" aria-hidden="true">
            {timeline.ticks.map((m) => (
              <span key={m} className="mono" style={{ left: ((m - lo) / (hi - lo)) * width }}>
                {clock(m)}
              </span>
            ))}
          </div>
          {timeline.lanes.map((lane) => {
            const xs = lane.events.map((event) => x(event.time))
            const placed = placeLabels(
              xs,
              lane.events.map((event) => measure(event.time, event.text)),
              width,
            )
            return (
              <div className="lane" key={lane.name}>
                <div className="lane-name">
                  {lane.name}
                  <small>{lane.sub}</small>
                </div>
                <div className="lane-track">
                  {lane.events.map((event, index) => (
                    <div
                      key={`${event.time}-${event.text}`}
                      className={`ev ${event.bad ? 'bad' : ''} ${event.shape}`}
                      style={{ left: xs[index] }}
                    >
                      <i aria-hidden="true" />
                      <span className={`ev-label ${placed[index].row} ${placed[index].side}`}>
                        <b className="mono">{event.time}</b>
                        {event.text}
                      </span>
                    </div>
                  ))}
                </div>
                <div className="lane-end">
                  {lane.end && (
                    <>
                      <b>{lane.end.strong}</b>
                      {lane.end.rest}
                    </>
                  )}
                </div>
              </div>
            )
          })}
          {timeline.brackets.length > 0 && (
            <div className="brackets">
              {timeline.brackets.map((bracket) => {
                const a = x(bracket.from)
                const b = x(bracket.to)
                return (
                  <div
                    key={bracket.kind}
                    className={`bracket ${bracket.kind} ${Math.min(a, b) > width / 2 ? 'flip' : ''}`}
                    style={{ left: Math.min(a, b), width: Math.abs(b - a) }}
                  >
                    <span>{bracket.text}</span>
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}
    </div>
  )
}

export default CaseTimeline
