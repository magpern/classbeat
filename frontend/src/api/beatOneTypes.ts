import { count, flag, isObject, list, object, optionalFlag, optionalText, text, type JsonObject } from './decode'

// The beat-one correction of a class (split out of classes.ts, MA3): what the current version
// plays and the corrections the user set, decoded defensively (anything malformed hides the control).

/** One song the current version plays, with the class's beat-one correction for it. */
export interface BeatOneSong {
  song: number
  start: number
  end: number
  bpm: number
  estimate: boolean
  /** A Spotify track's title; null for a song found in a Swedebeat mix. */
  title: string | null
  /** The correction the user set, -3 to 3 (0: none). */
  beats: number
  /** What the current version was fitted with. */
  applied: number
  /** The song's BPM is not the one the correction was set at: listen again. */
  bpmChanged: boolean
}

/** A correction for earlier music, or for a song the current version does not play. */
export interface BeatOneStale {
  id: string
  song: number
  beats: number
}

export interface BeatOne {
  available: boolean
  revisionId: string | null
  songs: BeatOneSong[]
  /** The corrections differ from what the current version was fitted with. */
  pending: boolean
  /** Corrections not used by the current version, each clearable by id. */
  stale: BeatOneStale[]
}

export const NO_BEAT_ONE: BeatOne = { available: false, revisionId: null, songs: [], pending: false, stale: [] }

function whole(source: JsonObject, key: string, low: number, high: number): number {
  const value = source[key]
  if (typeof value !== 'number' || !Number.isInteger(value) || value < low || value > high) {
    throw new Error(`bad ${key}`)
  }
  return value
}

export function decodeBeatOne(value: unknown): BeatOne {
  if (!isObject(value)) return NO_BEAT_ONE
  try {
    const songs = list(value, 'songs').map((entry) => {
      const item = object(entry, 'song')
      return {
        song: whole(item, 'song', 1, 500),
        start: count(item, 'start'),
        end: count(item, 'end'),
        bpm: count(item, 'bpm'),
        estimate: flag(item, 'estimate'),
        title: optionalText(item, 'title'),
        beats: whole(item, 'beats', -3, 3),
        applied: whole(item, 'applied', -3, 3),
        bpmChanged: optionalFlag(item, 'bpm_changed') ?? false,
      }
    })
    const stale = list(value, 'stale').map((entry) => {
      const item = object(entry, 'stale')
      return { id: text(item, 'id'), song: whole(item, 'song', 1, 500), beats: whole(item, 'beats', -3, 3) }
    })
    return {
      available: flag(value, 'available'),
      revisionId: optionalText(value, 'revision_id'),
      songs,
      pending: flag(value, 'pending'),
      stale,
    }
  } catch {
    return NO_BEAT_ONE // anything malformed hides the control
  }
}
