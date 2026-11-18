import type { DraftSegment } from '../../api/draftTypes'
import type { Song } from '../../api/songs'
import { segmentSeconds } from '../workout'

// The rows of the class's segment list (ADR 0038 section 10.2, pure): each segment with the songs
// that start inside it. A song belongs to the segment it starts in (the last one takes any that
// start after the end), so a long song is listed once; a segment where no song starts shows that
// the song before it plays on.

export interface SegmentRow {
  index: number
  name: string
  kind: string
  startSec: number
  endSec: number
  songs: Song[]
}

export function songRows(segments: readonly DraftSegment[], songs: readonly Song[] | null): SegmentRow[] {
  const ordered = [...(songs ?? [])].sort((a, b) => a.startSec - b.startSec)
  let at = 0
  return segments.map((segment, index) => {
    const startSec = at
    const endSec = at + segmentSeconds(segment)
    at = endSec
    const last = index === segments.length - 1
    const inside = ordered.filter((song) => song.startSec >= startSec && (last || song.startSec < endSec))
    return { index, name: segment.name, kind: segment.kind, startSec, endSec, songs: inside }
  })
}
