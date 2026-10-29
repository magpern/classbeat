import type { DraftSegment } from '../../api/jobs'
import { clock, totalSeconds } from '../../lib/workout'

// The class as a bar chart over time: one bar per interval, as wide as it is long and as tall as
// its effort zone. Colours come from the zone tokens (tokens.css); the table under the chart
// holds the same numbers for anyone who cannot see the chart. Drawn from stored numbers only.

const WIDTH = 600
const HEIGHT = 120
const TOP = 6

export function TimelineChart({ segments, title }: { segments: DraftSegment[]; title: string }) {
  const total = totalSeconds(segments)
  if (total <= 0) return null
  const scale = WIDTH / total
  let at = 0
  const bars: { x: number; w: number; zone: number; key: string }[] = []
  const edges: number[] = []
  segments.forEach((segment, s) => {
    if (s > 0) edges.push(at * scale)
    segment.intervals.forEach((interval, i) => {
      bars.push({ x: at * scale, w: interval.sec * scale, zone: interval.zone, key: `${String(s)}-${String(i)}` })
      at += interval.sec
    })
  })
  const label = `Effort over time for ${title}: ${String(segments.length)} segments, ${clock(total)} in total.`
  return (
    <figure className="timeline">
      <svg
        viewBox={`0 0 ${String(WIDTH)} ${String(HEIGHT)}`}
        role="img"
        aria-label={label}
        preserveAspectRatio="none"
        className="timeline__svg"
      >
        {bars.map((bar) => {
          const zone = Math.min(Math.max(Math.round(bar.zone), 1), 7)
          const height = ((HEIGHT - TOP) * zone) / 7
          return (
            <rect
              key={bar.key}
              className="timeline__bar"
              data-zone={zone}
              x={bar.x}
              y={HEIGHT - height}
              width={Math.max(bar.w - 0.5, 0.5)}
              height={height}
            />
          )
        })}
        {edges.map((x) => (
          <line key={x} className="timeline__edge" x1={x} x2={x} y1={0} y2={HEIGHT} />
        ))}
      </svg>
      <figcaption className="muted">
        Taller bars are harder zones (1 to 7). Lines mark the segments. {clock(total)} in total.
      </figcaption>
    </figure>
  )
}
