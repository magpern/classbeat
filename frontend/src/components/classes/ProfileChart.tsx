import { Flame, Gamepad2, Zap } from 'lucide-react'
import { useId, useState, type KeyboardEvent } from 'react'
import type { Song } from '../../api/songs'
import { useT } from '../../i18n/I18n'
import { cadenceText, flatten, heightShare, inclineText, zoneCount, zoneName, type Bar } from '../../lib/profile'
import { gameLabel, gameMarkerName } from '../../lib/games'
import { clock, totalSeconds } from '../../lib/workout'
import { isRunningDraft, type DraftSegment } from '../../api/draftTypes'
import { SongStrip } from './SongStrip'
import { zoneStopBottom, zoneStopTop } from './zoneClasses'

// The class profile on the stage (ADR 0013 section 7): x is time, bar height and colour are the
// zone, each bar fades from its zone colour to a 60/40 mix with the paper colour, a flame sits
// above zone 6 and a bolt above zone 7. A zone is never told by colour alone: height, the markers,
// the Z labels, the detail bar and the "Song by song" list under it all carry it.

const W = 600
const LEFT = 34
const PLOT_W = W - LEFT
const TOP = 6
const PLOT_H = 150
const MARKER = 18
/** Extra room on top when a game marker is stacked over a flame or a bolt. */
const GAME_ROOM = MARKER + 2
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
  const t = useT()
  const id = useId()
  const bars = flatten(segments)
  // A running class (intervals with an incline and no cadence): five zones, an incline instead of
  // cadence and position, and no flame or bolt (those mark the bike-only zones 6 and 7).
  const running = isRunningDraft(segments)
  const zones = Array.from({ length: zoneCount(running) }, (_, i) => i + 1)
  const total = totalSeconds(segments)
  const [selected, setSelected] = useState<number | null>(null)
  if (total <= 0) return null
  const scale = PLOT_W / total
  const AXIS_Y = TOP + (bars.some((b) => b.game !== undefined && b.zone >= 6) ? GAME_ROOM : 0) + PLOT_H
  const H = AXIS_Y + 26
  const hardest = Math.max(...bars.map((b) => b.zone))
  const summary = t(running ? 'class.chart.summaryRunning' : 'class.chart.summary', {
    title,
    segments: segments.length,
    total: clock(total),
    zone: hardest,
  })
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
  for (let at = TICK; at < total; at += TICK) ticks.push(at)
  const chosen: Bar | undefined = selected === null ? undefined : bars[selected]
  return (
    <figure className="m-0 flex flex-col gap-3">
      <div
        tabIndex={0}
        role="group"
        aria-label={t('class.chart.keys', { title })}
        onKeyDown={move}
        className="rounded-lg bg-stage p-3 text-stage-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
        data-testid="profile"
      >
        <svg viewBox={`0 0 ${String(W)} ${String(H)}`} role="img" aria-label={summary} className="block h-auto w-full">
          <defs>
            {zones.map((z) => (
              <linearGradient key={z} id={`${id}-z${String(z)}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" className={zoneStopTop(z)} />
                <stop offset="1" className={zoneStopBottom(z)} />
              </linearGradient>
            ))}
          </defs>
          {zones.map((z) => {
            const y = AXIS_Y - PLOT_H * heightShare(z)
            return (
              <g key={z}>
                <line x1={LEFT} x2={W} y1={y} y2={y} className="stroke-stage-grid" strokeWidth={1} />
                <text x={2} y={y + 4} fontSize={13} className="fill-stage-muted">
                  {t('class.zone.short', { zone: z })}
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
                  className={selected === i ? 'stroke-stage-text' : 'stroke-none'}
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
                    className="stroke-stage-text"
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
                    className="stroke-stage-text"
                  />
                ) : null}
                {bar.game !== undefined ? (
                  <Gamepad2
                    data-testid="marker-game"
                    data-game={bar.game}
                    role="img"
                    aria-label={gameMarkerName(bar.game, bar.sec)}
                    x={x + w / 2 - MARKER / 2}
                    y={y - MARKER - 2 - (bar.zone >= 6 ? MARKER + 2 : 0)}
                    width={MARKER}
                    height={MARKER}
                    className="stroke-stage-text"
                  />
                ) : null}
              </g>
            )
          })}
          <line x1={LEFT} x2={W} y1={AXIS_Y} y2={AXIS_Y} className="stroke-stage-muted" strokeWidth={1} />
          {ticks.map((tick) => (
            <g key={tick}>
              <line
                x1={LEFT + tick * scale}
                x2={LEFT + tick * scale}
                y1={AXIS_Y}
                y2={AXIS_Y + 4}
                className="stroke-stage-muted"
              />
              <text
                x={LEFT + tick * scale}
                y={AXIS_Y + 20}
                fontSize={13}
                textAnchor="middle"
                className="fill-stage-muted"
              >
                {t('class.chart.minutes', { minutes: tick / 60 })}
              </text>
            </g>
          ))}
        </svg>
        {songs !== null && songs.length > 0 ? <SongStrip songs={songs} total={total} left={LEFT / W} /> : null}
      </div>
      <figcaption className="m-0 rounded-md bg-raised px-3 py-2 text-[0.9375rem] text-text" data-testid="profile-detail" aria-live="polite">
        {chosen === undefined ? (
          <span className="text-muted">
            {t('class.chart.select')}
          </span>
        ) : (
          <>
            <strong>
              {t('class.chart.zoneName', { zone: chosen.zone, name: zoneName(chosen.zone) })}
            </strong>
            {', '}
            {t('class.chart.range', { from: clock(chosen.start), to: clock(chosen.start + chosen.sec) })}
            {', '}
            {running ? (
              inclineText(chosen.gradient).toLowerCase()
            ) : (
              <>
                {cadenceText(chosen.rpm)}
                {', '}
                {chosen.standing ? t('class.position.standingLower') : t('class.position.seatedLower')}
              </>
            )}
            {chosen.game !== undefined ? <>{', '}{gameLabel(chosen.game)}</> : null}
            {chosen.cue ? <>{': '}{chosen.cue}</> : null}
          </>
        )}
      </figcaption>
    </figure>
  )
}
