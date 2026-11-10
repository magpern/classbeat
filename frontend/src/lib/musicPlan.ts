import type { DraftSegment } from '../api/jobs'
import type { Song } from '../api/songs'
import { segmentSeconds } from './workout'

// The planned songs of each section of a class (MA4, pure). The only data is what the approved
// song list holds: a title, an artist, a BPM, a length and where the song starts in the class. A
// song belongs to every section it overlaps, so a long one that runs over a change of section is
// listed in both. Nothing here says how the music sounds.

export interface PlannedSection {
  index: number
  name: string
  kind: string
  startSec: number
  endSec: number
  /** The songs that overlap the section, with their start moved to the section's own clock (0 at its start). */
  songs: Song[]
}

/** Where a song's block ends: the next song's start, or its own length for the last one (as `SongStrip` draws it). */
function blockEnd(songs: readonly Song[], i: number): number {
  const song = songs[i]!
  return songs[i + 1]?.startSec ?? song.startSec + song.lengthSec
}

export function planSections(segments: readonly DraftSegment[], songs: readonly Song[]): PlannedSection[] {
  const ordered = [...songs].sort((a, b) => a.startSec - b.startSec)
  let at = 0
  return segments.map((segment, index) => {
    const startSec = at
    const endSec = at + segmentSeconds(segment)
    at = endSec
    const inside = ordered
      .filter((song, i) => endSec > startSec && song.startSec < endSec && blockEnd(ordered, i) > startSec)
      .map((song) => ({ ...song, startSec: Math.max(song.startSec - startSec, 0) }))
    return { index, name: segment.name, kind: segment.kind, startSec, endSec, songs: inside }
  })
}
