import { lanesAt, type Lane, type SpotifyModel } from './timeModel'

// The Spotify class on one strip (ADR 0042 section 7.3). Pure: the model in, positions out. Songs
// take two rows by turns so an overlap, where the rider's own crossfade plays, is visible and is
// hatched; the cursor is a class instant from the transport, drawn by the page.

export interface Bar {
  piece: number
  label: string
  startMs: number
  endMs: number
  leftPct: number
  widthPct: number
  /** 0 or 1: the row of the strip, alternating by song. */
  row: 0 | 1
}

export interface OverlapBar {
  outgoing: number
  incoming: number
  startMs: number
  endMs: number
  leftPct: number
  widthPct: number
}

export interface Layout {
  totalMs: number
  bars: Bar[]
  overlaps: OverlapBar[]
}

const pct = (ms: number, total: number): number => (total <= 0 ? 0 : (ms / total) * 100)

export function layoutOf(model: SpotifyModel): Layout {
  const totalMs = model.windows.reduce((end, w) => Math.max(end, w.endMs), 0)
  const bars = model.windows.map((w, i): Bar => ({
    piece: w.piece,
    label: w.label,
    startMs: w.startMs,
    endMs: w.endMs,
    leftPct: pct(w.startMs, totalMs),
    widthPct: pct(w.endMs - w.startMs, totalMs),
    row: i % 2 === 0 ? 0 : 1,
  }))
  const overlaps = model.overlaps.map((o): OverlapBar => ({
    ...o,
    leftPct: pct(o.startMs, totalMs),
    widthPct: pct(o.endMs - o.startMs, totalMs),
  }))
  return { totalMs, bars, overlaps }
}

/** Where the cursor sits on the strip, in percent; null: no cursor. */
export function cursorPct(layout: Layout, classMs: number | null): number | null {
  return classMs === null ? null : Math.min(100, Math.max(0, pct(classMs, layout.totalMs)))
}

/** The class instant at `fraction` (0 to 1) across a bar, kept inside the bar. */
export function classMsAt(bar: Pick<Bar, 'startMs' | 'endMs'>, fraction: number): number {
  const f = Number.isFinite(fraction) ? Math.min(1, Math.max(0, fraction)) : 0
  return Math.round(bar.startMs + f * (bar.endMs - bar.startMs))
}

export interface PlayTarget {
  piece: number
  sourceMs: number
  classMs: number
}

const targetOf = (lane: Lane, classMs: number): PlayTarget => ({ piece: lane.piece, sourceMs: lane.sourceMs, classMs })

/**
 * What a click at `classMs` plays: the only song there, or inside an overlap the song the person
 * chose (`incoming` by default, or `outgoing`). Null: no song is at that instant.
 */
export function playTarget(model: SpotifyModel, classMs: number, pick: 'incoming' | 'outgoing' = 'incoming'): PlayTarget | null {
  const lanes = lanesAt(model, classMs)
  if (lanes.length === 0) return null
  const wanted = lanes.length === 1 ? lanes[0] : lanes.find((l) => l.role === pick)
  return wanted === undefined ? null : targetOf(wanted, classMs)
}
