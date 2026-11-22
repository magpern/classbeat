// The class time model (ADR 0042 section 2). Pure: no React, no fetch, no clock.
//
//   classMs      time in the class, 0 to the total
//   piece        a play-order index; its recording is modelled by kind, index and label, because
//                the API sends the client no catalogue reference or Spotify id
//   sourceMs     a position in the analysed file of that recording
//   transportMs  the position a transport plays (the rendered clip, or the Spotify recording)
//
// Every mapping result carries its provenance; the analysis fact and the instructor's correction
// are never merged into one number. What the API does not tell the client is `null`, shown as
// "not recorded", never invented.

export interface Provenance {
  analysis: { mapVersion: string | null; spotdownJob: string | null; timingClass: string | null }
  alignment: { offsetMs: number | null; source: string | null }
  correction: { offsetMs: number; setAgainstAnalysis: string | null } | null
}

/** The provenance of a result the API gave no analysis facts for. */
export const NO_PROVENANCE: Provenance = {
  analysis: { mapVersion: null, spotdownJob: null, timingClass: null },
  alignment: { offsetMs: null, source: null },
  correction: null,
}

export type RecordingKind = 'swedebeat' | 'spotify'

export interface Recording {
  kind: RecordingKind
  /** The play-order index of the piece that plays it. */
  piece: number
  label: string
}

// The studio mapping -----------------------------------------------------------------------------

/** One clip of the rendered mix, in class time. */
export interface StudioClip {
  piece: number
  label: string
  startMs: number
  endMs: number
  /** The equal-power fade into the clip, at its start; 0: none. */
  fadeInMs: number
  /** The equal-power fade out of the clip, at its end; 0: none. */
  fadeOutMs: number
  /** Where in the analysed file the clip begins; null: the API does not say. */
  sourceStartMs: number | null
  provenance?: Provenance
}

export interface PieceMapping {
  piece: number
  recording: Recording
  transportMs: number
  sourceMs: number | null
  /** The linear gain of the piece at this instant (equal power in a crossfade). */
  gain: number
  role: 'solo' | 'incoming' | 'outgoing'
  provenance: Provenance
}

/**
 * The gain of a clip at `classMs`: the render's equal-power curves (`renderchild.py`). Incoming is
 * `sin(pi/2 * x / fade_in)`, outgoing is `cos(pi/2 * (x - (len - fade_out)) / fade_out)`, 1 between.
 */
export function studioGain(clip: StudioClip, classMs: number): number {
  const x = classMs - clip.startMs
  const len = clip.endMs - clip.startMs
  let gain = 1
  if (clip.fadeInMs > 0 && x < clip.fadeInMs) gain *= Math.sin((Math.PI / 2) * (x / clip.fadeInMs))
  const outAt = len - clip.fadeOutMs
  if (clip.fadeOutMs > 0 && x > outAt) gain *= Math.cos((Math.PI / 2) * ((x - outAt) / clip.fadeOutMs))
  return Math.max(0, Math.min(1, gain))
}

/**
 * `classMs` to the pieces audible then: one piece in the body of a song, BOTH pieces at a
 * crossfade instant. `transportMs = classMs - clip.startMs`. Clips are half-open `[start, end)`.
 */
export function mapStudio(clips: readonly StudioClip[], classMs: number): PieceMapping[] {
  const audible = clips.filter((clip) => classMs >= clip.startMs && classMs < clip.endMs)
  return audible.map((clip) => {
    const transportMs = classMs - clip.startMs
    const inFade = clip.fadeInMs > 0 && transportMs < clip.fadeInMs
    const outFade = clip.fadeOutMs > 0 && classMs >= clip.endMs - clip.fadeOutMs
    const role = audible.length > 1 && inFade ? 'incoming' : audible.length > 1 && outFade ? 'outgoing' : 'solo'
    return {
      piece: clip.piece,
      recording: { kind: 'swedebeat', piece: clip.piece, label: clip.label },
      transportMs,
      sourceMs: clip.sourceStartMs === null ? null : clip.sourceStartMs + transportMs,
      gain: studioGain(clip, classMs),
      role,
      provenance: clip.provenance ?? NO_PROVENANCE,
    }
  })
}

/** The class instant of a position `transportMs` into the rendered clip of `piece`; null for an unknown piece. */
export function studioClassMs(clips: readonly StudioClip[], piece: number, transportMs: number): number | null {
  const clip = clips.find((c) => c.piece === piece)
  return clip === undefined ? null : clip.startMs + transportMs
}

// The Spotify mapping ----------------------------------------------------------------------------

/** One song of a Spotify class, in play order. */
export interface SpotifySong {
  piece: number
  label: string
  /** The song's length in the class, `L_i`, in ms. */
  lengthMs: number
  /** The spotdown alignment of the recording; null: none. */
  alignmentOffsetMs: number | null
  /** The instructor's correction; 0: none. */
  correctionOffsetMs: number
  timingClass: string | null
  provenance?: Provenance
}

export interface SpotifyWindow {
  piece: number
  label: string
  /** `S_i`: where the song starts in the class. */
  startMs: number
  /** `E_i = S_i + L_i`. */
  endMs: number
}

/** An overlap region `[S_{i+1}, E_i)`: the rider's own Spotify crossfades there. */
export interface OverlapRegion {
  outgoing: number
  incoming: number
  startMs: number
  endMs: number
}

export interface SpotifyModel {
  /** The class's crossfade constant `C`, read from its music map, in ms. */
  crossfadeMs: number
  songs: readonly SpotifySong[]
  windows: readonly SpotifyWindow[]
  overlaps: readonly OverlapRegion[]
}

/**
 * `S_0 = 0`, `E_i = S_i + L_i`, `S_{i+1} = E_i - C`. `crossfadeMs` is an input: it is read from the
 * class's map and never hard-coded here.
 */
export function buildSpotifyModel(songs: readonly SpotifySong[], crossfadeMs: number): SpotifyModel {
  const windows: SpotifyWindow[] = []
  let start = 0
  for (const song of songs) {
    const end = start + song.lengthMs
    windows.push({ piece: song.piece, label: song.label, startMs: start, endMs: end })
    start = end - crossfadeMs
  }
  const overlaps = windows.slice(1).flatMap((next, i): OverlapRegion[] => {
    const prev = windows[i]
    return prev === undefined || next.startMs >= prev.endMs
      ? []
      : [{ outgoing: prev.piece, incoming: next.piece, startMs: next.startMs, endMs: prev.endMs }]
  })
  return { crossfadeMs, songs, windows, overlaps }
}

export interface Lane {
  piece: number
  /** The position in the song's analysed file (`t - S_i`). */
  sourceMs: number
  role: 'only' | 'incoming' | 'outgoing'
  /** The lane a click plays unless the user picks the other: the incoming song. */
  isDefault: boolean
}

/** The lanes at class instant `t`: one outside an overlap; inside one, the incoming song first (the default), then the outgoing. */
export function lanesAt(model: SpotifyModel, classMs: number): Lane[] {
  const inside = model.windows.filter((w) => classMs >= w.startMs && classMs < w.endMs)
  const lanes = inside.map((w, i): Lane => {
    const role = inside.length === 1 ? 'only' : i === inside.length - 1 ? 'incoming' : 'outgoing'
    return { piece: w.piece, sourceMs: classMs - w.startMs, role, isDefault: inside.length === 1 || role === 'incoming' }
  })
  return lanes.sort((a, b) => Number(b.isDefault) - Number(a.isDefault))
}

/** What the Spotify transport reports: the playing track as the requested recording or not, and its position. */
export interface SpotifyObservation {
  /** True: the playing track is the requested recording. */
  requestedTrack: boolean
  transportMs: number
}

export type SpotifyCursor =
  | { status: 'external' }
  | { status: 'unaligned' }
  | { status: 'outside_window'; sourceMs: number }
  | { status: 'playing'; classMs: number; sourceMs: number; inOverlap: boolean }

/**
 * The cursor from an observed position, in this order: a track that is not the requested one is
 * `external`; an `unusable` timing class or no alignment is `unaligned`; otherwise
 * `sourceMs = transportMs - alignment - correction` and `classMs = S_i + sourceMs`, a cursor inside
 * `[S_i, E_i)` and `outside_window` outside it.
 */
export function spotifyCursor(model: SpotifyModel, piece: number, observed: SpotifyObservation): SpotifyCursor {
  if (!observed.requestedTrack) return { status: 'external' }
  const song = model.songs.find((s) => s.piece === piece)
  const window = model.windows.find((w) => w.piece === piece)
  if (song === undefined || window === undefined) return { status: 'external' }
  if (song.timingClass === 'unusable' || song.alignmentOffsetMs === null) return { status: 'unaligned' }
  const sourceMs = observed.transportMs - song.alignmentOffsetMs - song.correctionOffsetMs
  const classMs = window.startMs + sourceMs
  if (classMs < window.startMs || classMs >= window.endMs) return { status: 'outside_window', sourceMs }
  const inOverlap = model.overlaps.some((o) => classMs >= o.startMs && classMs < o.endMs)
  return { status: 'playing', classMs, sourceMs, inOverlap }
}
