import { Flame, Zap } from 'lucide-react'
import { useId, useState, type KeyboardEvent } from 'react'
import type { Song } from '../../api/songs'
import { cadenceText, flatten, heightShare, zoneName, type Bar } from '../../lib/profile'
import { clock, totalSeconds } from '../../lib/workout'
import type { DraftSegment } from '../../api/jobs'
import { SongStrip } from './SongStrip'

// The class profile on the stage (ADR 0013 section 7): x is time, bar height and colour are the
// zone, each bar fades from its zone colour to a 60/40 mix with the paper colour, a flame sits
// above zone 6 and a bolt above zone 7. A zone is never told by colour alone: height, the markers,
// the Z labels, the detail bar and the "Song by song" list under it all carry it.

const W = 600
const LEFT = 34
const PLOT_W = W - LEFT
const TOP = 6
const PLOT_H = 150
const AXIS_Y = TOP + PLOT_H
const H = AXIS_Y + 26
const MARKER = 18
const TICK = 300

export function ProfileChart({
  segments,
  title,
  songs,
}: {
  segments: DraftSegment[]
  title: string
  songs: Song[] | null
}) {
  const id = useId()
  const bars = flatten(segments)
  const total = totalSeconds(segments)
  const [selected, setSelected] = useState<number | null>(null)
  if (total <= 0) return null
  const scale = PLOT_W / total
  const hardest = Math.max(...bars.map((b) => b.zone))
  const summary = `Effort over time for ${title}: ${String(segments.length)} segments, ${clock(total)} in total. The hardest zone is ${String(hardest)}.`
  const move = (event: KeyboardEvent) => {
    const last = bars.length - 1
    let next: number | null
    if (event.key === 'ArrowRight') next = selected === null ? 0 : Math.min(selected + 1, last)
    else if (event.key === 'ArrowLeft') next = selected === null ? 0 : Math.max(selected - 1, 0)
    else if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = last
    else if (event.key === 'Escape') next = null
    else return
    event.preventDefault()
    setSelected(next)
  }
  const ticks: number[] = []
  for (let t = TICK; t < total; t += TICK) ticks.push(t)
  const chosen: Bar | undefined = selected === null ? undefined : bars[selected]
  return (
    <figure className="m-0 flex flex-col gap-3">
      <div
        tabIndex={0}
        role="group"
        aria-label={`${title}: use the left and right arrow keys to look at each interval`}
        onKeyDown={move}
        className="rounded-lg bg-stage p-3 text-stage-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
        data-testid="profile"
      >
        <svg viewBox={`0 0 ${String(W)} ${String(H)}`} role="img" aria-label={summary} className="block h-auto w-full">
          <defs>
            {[1, 2, 3, 4, 5, 6, 7].map((z) => (
              <linearGradient key={z} id={`${id}-z${String(z)}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" style={{ stopColor: `var(--cb-zone-${String(z)})` }} />
                <stop
                  offset="1"
                  style={{ stopColor: `color-mix(in srgb, var(--cb-zone-${String(z)}) 60%, var(--cb-paper))` }}
                />
              </linearGradient>
            ))}
          </defs>
          {[1, 2, 3, 4, 5, 6, 7].map((z) => {
            const y = AXIS_Y - PLOT_H * heightShare(z)
            return (
              <g key={z}>
                <line x1={LEFT} x2={W} y1={y} y2={y} style={{ stroke: 'var(--cb-stage-grid)' }} strokeWidth={1} />
                <text x={2} y={y + 4} fontSize={13} style={{ fill: 'var(--cb-stage-muted)' }}>
                  {`Z${String(z)}`}
                </text>
              </g>
            )
          })}
          {bars.map((bar, i) => {
            const height = PLOT_H * heightShare(bar.zone)
            const x = LEFT + bar.start * scale
            const w = Math.max(bar.sec * scale - 1, 1)
            const y = AXIS_Y - height
            return (
              <g key={i}>
                <rect
                  data-bar={i}
                  data-zone={bar.zone}
                  x={x}
                  y={y}
                  width={w}
                  height={height}
                  fill={`url(#${id}-z${String(bar.zone)})`}
                  stroke={selected === i ? 'var(--cb-stage-text)' : 'none'}
                  strokeWidth={2}
                  onMouseEnter={() => {
                    setSelected(i)
                  }}
                  onClick={() => {
                    setSelected(i)
                  }}
                />
                {bar.zone === 6 ? (
                  <Flame
                    data-testid="marker-flame"
                    aria-hidden="true"
                    x={x + w / 2 - MARKER / 2}
                    y={y - MARKER - 2}
                    width={MARKER}
                    height={MARKER}
                    style={{ stroke: 'var(--cb-stage-text)' }}
                  />
                ) : null}
                {bar.zone === 7 ? (
                  <Zap
                    data-testid="marker-bolt"
                    aria-hidden="true"
                    x={x + w / 2 - MARKER / 2}
                    y={y - MARKER - 2}
                    width={MARKER}
                    height={MARKER}
                    style={{ stroke: 'var(--cb-stage-text)' }}
                  />
                ) : null}
              </g>
            )
          })}
          <line x1={LEFT} x2={W} y1={AXIS_Y} y2={AXIS_Y} style={{ stroke: 'var(--cb-stage-muted)' }} strokeWidth={1} />
          {ticks.map((t) => (
            <g key={t}>
              <line
                x1={LEFT + t * scale}
                x2={LEFT + t * scale}
                y1={AXIS_Y}
                y2={AXIS_Y + 4}
                style={{ stroke: 'var(--cb-stage-muted)' }}
              />
              <text
                x={LEFT + t * scale}
                y={AXIS_Y + 20}
                fontSize={13}
                textAnchor="middle"
                style={{ fill: 'var(--cb-stage-muted)' }}
              >
                {`${String(t / 60)} min`}
              </text>
            </g>
          ))}
        </svg>
        {songs !== null && songs.length > 0 ? <SongStrip songs={songs} total={total} left={LEFT / W} /> : null}
      </div>
      <figcaption className="m-0 rounded-md bg-raised px-3 py-2 text-[0.9375rem] text-text" data-testid="profile-detail" aria-live="polite">
        {chosen === undefined ? (
          <span className="text-muted">
            Select a bar, or focus the chart and use the arrow keys, to see an interval.
          </span>
        ) : (
          <>
            <strong>
              Zone {String(chosen.zone)} {zoneName(chosen.zone)}
            </strong>
            {', '}
            {clock(chosen.start)} to {clock(chosen.start + chosen.sec)}
            {', '}
            {cadenceText(chosen.rpm)}
            {', '}
            {chosen.standing ? 'standing' : 'seated'}
            {chosen.cue ? <>{': '}{chosen.cue}</> : null}
          </>
        )}
      </figcaption>
    </figure>
  )
}
