import type { Alignment, AlignmentSong } from '../api/spotifyPreview'
import { buildSpotifyModel, type Provenance, type SpotifyModel, type SpotifySong } from './timeModel'

// The API's alignment answer as the preview time model's input (ADR 0042 sections 2 and 7.4). Pure.
//
// spotdown's alignment and the instructor's correction stay two numbers: the model subtracts each
// on its own, and the provenance carries both. A correction that was set against another analysis
// (`listen_again`) is shown but NOT applied: its offset is 0 in the model until it is confirmed again.

/** The nudge steps, in ms (ADR 0042 section 7.4): 0.1, 0.5, 1, 5 and 10 seconds. */
export const NUDGE_STEPS_MS: readonly number[] = [100, 500, 1000, 5000, 10000]

/** The most a correction may be, either way, in ms (the table's check). */
export const MAX_CORRECTION_MS = 60_000

/** A short, stable label for what a correction was set against (the map version and the start of the file hash). */
export function setAgainstLabel(against: { mapVersion: number | null; sha256: string }): string {
  return `${against.mapVersion === null ? '-' : `v${String(against.mapVersion)}`} ${against.sha256.slice(0, 12)}`
}

export function provenanceOf(song: AlignmentSong, mapVersion: number | null): Provenance {
  const c = song.correction
  return {
    analysis: {
      mapVersion: mapVersion === null ? null : String(mapVersion),
      spotdownJob: song.analysis.spotdownJob,
      timingClass: song.analysis.timingClass,
    },
    alignment: { offsetMs: song.alignmentOffsetMs, source: song.alignmentOffsetMs === null ? null : 'spotdown' },
    correction: c === null ? null : { offsetMs: c.offsetMs, setAgainstAnalysis: c.setAgainst === null ? null : setAgainstLabel(c.setAgainst) },
  }
}

/** The correction the cursor applies: the stored one when it still holds, else none. */
export function appliedCorrectionMs(song: AlignmentSong): number {
  return song.correction !== null && song.correction.state === 'current' ? song.correction.offsetMs : 0
}

export function spotifySongOf(song: AlignmentSong, mapVersion: number | null): SpotifySong {
  return {
    piece: song.piece,
    label: song.title,
    lengthMs: song.lengthMs,
    alignmentOffsetMs: song.alignmentOffsetMs,
    correctionOffsetMs: appliedCorrectionMs(song),
    timingClass: song.analysis.timingClass,
    provenance: provenanceOf(song, mapVersion),
  }
}

/** The class's Spotify model: `S_0 = 0`, `E_i = S_i + L_i`, `S_{i+1} = E_i - C` with `C` from the class's own map. */
export function modelOf(alignment: Alignment): SpotifyModel {
  return buildSpotifyModel(
    alignment.songs.map((s) => spotifySongOf(s, alignment.mapVersion)),
    alignment.crossfadeMs,
  )
}

/** The position in Spotify's recording that plays `sourceMs` of the analysed file: the inverse of the cursor mapping. */
export function transportPositionMs(song: SpotifySong, sourceMs: number): number {
  return Math.max(0, Math.round(sourceMs + (song.alignmentOffsetMs ?? 0) + song.correctionOffsetMs))
}

/** The correction after a nudge of `deltaMs`: clamped to a minute either way; `null` (clear it) when it comes to zero. */
export function nudged(currentMs: number, deltaMs: number): number | null {
  const next = Math.max(-MAX_CORRECTION_MS, Math.min(MAX_CORRECTION_MS, currentMs + deltaMs))
  return next === 0 ? null : next
}
