import { isObject } from './decode'

// The composer's part of a reading (ADR 0038 section 3; backend
// `design/interpret_composer_policy.py`): the songs the instructor named and the follow-up
// questions. A pin's title, artist and place are substrings of the instructor's own request, so
// they are shown as plain text; a question is a closed path and code whose wording comes from the
// catalogue. Empty for every class that was not started with the composer flow.

export interface PinnedSong {
  title: string
  artist: string | null
  /** Where the song goes, in the request's words ("the last climb"); null when not said. */
  at: string | null
}

export type QuestionCode = 'how_long' | 'how_hard'
export type Effort = 'easy' | 'moderate' | 'hard'

export type ReadingQuestion =
  | { question: 'how_long'; assumed: number }
  | { question: 'how_hard'; assumed: Effort }

export interface ReadingExtras {
  pinnedSongs: PinnedSong[]
  questions: ReadingQuestion[]
}

export const NO_EXTRAS: ReadingExtras = { pinnedSongs: [], questions: [] }

const EFFORTS: readonly string[] = ['easy', 'moderate', 'hard']

function words(source: Record<string, unknown>, key: string): string | null {
  const value = source[key]
  return typeof value === 'string' && value !== '' ? value : null
}

/** The extras of an interpretation answer; an entry that does not check is left out. */
export function decodeReadingExtras(body: Record<string, unknown>): ReadingExtras {
  const pinnedSongs: PinnedSong[] = []
  for (const entry of Array.isArray(body.pinned_songs) ? (body.pinned_songs as unknown[]) : []) {
    if (!isObject(entry)) continue
    const title = words(entry, 'title')
    if (title === null) continue
    pinnedSongs.push({ title, artist: words(entry, 'artist'), at: words(entry, 'at') })
  }
  const questions: ReadingQuestion[] = []
  for (const entry of Array.isArray(body.questions) ? (body.questions as unknown[]) : []) {
    if (!isObject(entry)) continue
    const assumed = entry.assumed
    if (entry.question === 'how_long' && typeof assumed === 'number' && Number.isInteger(assumed)) {
      questions.push({ question: 'how_long', assumed })
    } else if (entry.question === 'how_hard' && typeof assumed === 'string' && EFFORTS.includes(assumed)) {
      questions.push({ question: 'how_hard', assumed: assumed as Effort })
    }
  }
  return { pinnedSongs, questions }
}
