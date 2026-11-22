import type { EventKind } from '../../api/classEdits'
import type { Timeline, TimelineTrack } from '../../api/studioOutputs'

// "Snap to music": the downbeats a block edge can sit on. The grid is the piece's bar grid from the
// composed map's first downbeat (class time) and the song's tempo, four beats to a bar. The
// server validates the event against the analysed song (its window, its file) and rounds the edge
// to a whole second, so a candidate is a proposal, not a promise. Pure.

export interface SnapCandidate {
  piece: number
  sourceMs: number
  kind: EventKind
  /** The class second the downbeat falls on. */
  classSec: number
  /** Its distance from the edge, in seconds (signed: positive is later). */
  deltaSec: number
}

function barSec(track: TimelineTrack): number | null {
  return track.bpm > 0 ? 240 / track.bpm : null
}

/** The piece that is playing at class second `sec`: the incoming one in a crossfade. */
function pieceAt(tracks: readonly TimelineTrack[], sec: number): TimelineTrack | undefined {
  const inside = tracks.filter((t) => sec >= t.mixStartSec && sec < t.mixEndSec)
  return inside[inside.length - 1] ?? tracks[tracks.length - 1]
}

/** The `count` downbeats nearest to class second `edgeSec` in the piece under it, nearest first; none without a grid. */
export function snapCandidates(timeline: Timeline, edgeSec: number, count = 3): SnapCandidate[] {
  const track = pieceAt(timeline.tracks, edgeSec)
  const bar = track === undefined ? null : barSec(track)
  if (track === undefined || bar === null || track.firstDownbeatSec === undefined) return []
  const first = track.firstDownbeatSec
  const centre = Math.round((edgeSec - first) / bar)
  const found: SnapCandidate[] = []
  for (let n = centre - count; n <= centre + count; n += 1) {
    const classSec = first + n * bar
    if (classSec < track.mixStartSec || classSec >= track.mixEndSec) continue
    const sourceMs = Math.round(track.startSec * 1000 + (classSec - track.mixStartSec) * 1000)
    found.push({ piece: track.position, sourceMs, kind: 'downbeat', classSec, deltaSec: classSec - edgeSec })
  }
  return found.sort((a, b) => Math.abs(a.deltaSec) - Math.abs(b.deltaSec)).slice(0, count)
}
