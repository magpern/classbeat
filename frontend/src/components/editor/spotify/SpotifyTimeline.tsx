import type { MouseEvent } from 'react'
import { useT } from '../../../i18n/I18n'
import { classMsAt, cursorPct, layoutOf, type Bar } from '../../../preview/spotifyTimelineModel'
import type { SpotifyView } from '../../../preview/spotifyConnectTransport'
import type { SpotifyModel } from '../../../preview/timeModel'

const ROW_PX = 30
const HATCH = 'repeating-linear-gradient(135deg, var(--color-line) 0 3px, transparent 3px 7px)'

interface Props {
  model: SpotifyModel
  view: SpotifyView
  /** The user clicked the strip at `classMs`: Play only ever starts from such a gesture. */
  onPlayAt: (classMs: number) => void
  /** The user clicked an overlap: they choose which song to hear. */
  onOverlap: (classMs: number) => void
}

/** The fraction across `bar` a click landed at; the bar's start when the box has no width (a keyboard press). */
function fractionOf(event: MouseEvent<HTMLElement>): number {
  const box = event.currentTarget.getBoundingClientRect()
  return box.width > 0 && event.detail > 0 ? (event.clientX - box.left) / box.width : 0
}

/**
 * The class by song, two rows so overlaps show; an overlap is hatched. Clicking a song plays it
 * from there; clicking an overlap asks which of its two songs. The cursor and its band come from the
 * transport's observation only; inside an overlap the band says it is approximate.
 */
export function SpotifyTimeline({ model, view, onPlayAt, onOverlap }: Props) {
  const t = useT()
  const layout = layoutOf(model)
  const at = cursorPct(layout, view.classMs)
  const band = view.band === null ? null : { from: cursorPct(layout, view.band.fromMs) ?? 0, to: cursorPct(layout, view.band.toMs) ?? 0 }
  const number = (piece: number): number => piece + 1
  const press = (bar: Bar, event: MouseEvent<HTMLElement>) => onPlayAt(classMsAt(bar, fractionOf(event)))
  return (
    <div role="group" aria-label={t('preview.timeline.label')} className="relative w-full" style={{ height: ROW_PX * 2 + 4 }} data-testid="spotify-timeline">
      {layout.bars.map((bar) => (
        <button
          key={bar.piece}
          type="button"
          className="absolute overflow-hidden text-ellipsis whitespace-nowrap rounded-md border border-solid border-line bg-raised px-2 text-left text-[0.8125rem] text-text hover:bg-line"
          style={{ left: `${String(bar.leftPct)}%`, width: `${String(bar.widthPct)}%`, top: bar.row * (ROW_PX + 4), height: ROW_PX }}
          aria-label={t('preview.timeline.song', { n: number(bar.piece), title: bar.label })}
          data-testid={`spotify-song-${String(bar.piece)}`}
          onClick={(event) => press(bar, event)}
        >
          {number(bar.piece)}. {bar.label}
        </button>
      ))}
      {layout.overlaps.map((o) => (
        <button
          key={`${String(o.outgoing)}-${String(o.incoming)}`}
          type="button"
          className="absolute top-0 h-full cursor-pointer rounded-sm border border-dashed border-text bg-transparent p-0"
          style={{ left: `${String(o.leftPct)}%`, width: `${String(Math.max(o.widthPct, 0.8))}%`, backgroundImage: HATCH }}
          aria-label={t('preview.timeline.overlap', { a: number(o.outgoing), b: number(o.incoming) })}
          data-testid={`spotify-overlap-${String(o.outgoing)}`}
          onClick={(event) => onOverlap(classMsAt(o, fractionOf(event)))}
        />
      ))}
      {band === null ? null : (
        <div
          aria-hidden="true"
          className={view.inOverlap ? 'pointer-events-none absolute top-0 h-full bg-heat/40' : 'pointer-events-none absolute top-0 h-full bg-heat/25'}
          style={{ left: `${String(band.from)}%`, width: `${String(Math.max(band.to - band.from, 0.2))}%` }}
          data-testid="spotify-band"
          data-overlap={view.inOverlap ? 'true' : 'false'}
        />
      )}
      {at === null ? null : (
        <div aria-hidden="true" className="pointer-events-none absolute top-0 h-full w-0.5 bg-heat" style={{ left: `${String(at)}%` }} data-testid="spotify-cursor-line" />
      )}
    </div>
  )
}
