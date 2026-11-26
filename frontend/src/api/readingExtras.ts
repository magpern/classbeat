import { isObject } from './decode'

// The composer's part of a reading (ADR 0038 section 3; backend
// `design/interpret_composer_policy.py`): the songs the instructor named and the follow-up
// questions. A pin's title, artist and place are substrings of the instructor's own request, so
// they are shown as plain text; a question's text and options are the model's words, cleaned and capped by the backend
// and shown as plain text. Empty for every class that was not started with the composer flow.

export interface PinnedSong {
  title: string
  artist: string | null
  /** Where the song goes, in the request's words ("the last climb"); null when not said. */
  at: string | null
}

export type QuestionTopic = 'music' | 'length' | 'intensity' | 'structure' | 'sport' | 'other'

/** A question the model chose (backend `Question`): its text and options are plain text. */
export interface ReadingQuestion {
  topic: QuestionTopic
  text: string
  options: string[]
  assumedIndex: number
}

const TOPICS: readonly string[] = ['music', 'length', 'intensity', 'structure', 'sport', 'other']
const MIN_OPTIONS = 2
const MAX_OPTIONS = 5

export interface ReadingExtras {
  pinnedSongs: PinnedSong[]
  questions: ReadingQuestion[]
}

export const NO_EXTRAS: ReadingExtras = { pinnedSongs: [], questions: [] }

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
    const { topic, text, options, assumed_index: assumedIndex } = entry
    if (typeof topic !== 'string' || !TOPICS.includes(topic)) continue
    if (typeof text !== 'string' || text === '') continue
    if (!Array.isArray(options) || options.length < MIN_OPTIONS || options.length > MAX_OPTIONS) continue
    if (!options.every((o): o is string => typeof o === 'string' && o !== '')) continue
    if (typeof assumedIndex !== 'number' || !Number.isInteger(assumedIndex) || assumedIndex < 0 || assumedIndex >= options.length) continue
    questions.push({ topic: topic as QuestionTopic, text, options, assumedIndex })
  }
  return { pinnedSongs, questions }
}
