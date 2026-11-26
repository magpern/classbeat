import { isObject } from './decode'

// The extras of a reading: the songs the instructor named (ADR 0038 section 3, a composer class
// only; backend `design/interpret_composer_policy.py`) and the reading's questions (#1536, every
// reading; backend `design/reading_question_policy.py`). A pin's title, artist and place are
// substrings of the instructor's own request, so they are shown as plain text; a question's text
// and options are the model's words, cleaned and capped by the backend and shown as plain text,
// or a closed `code` the page phrases in its own words.

export interface PinnedSong {
  title: string
  artist: string | null
  /** Where the song goes, in the request's words ("the last climb"); null when not said. */
  at: string | null
}

export type QuestionTopic = 'music' | 'length' | 'intensity' | 'structure' | 'sport' | 'other'

/** The closed questions the backend adds in no model's words (`reading_question_policy.WHICH_MUSIC`). */
export type QuestionCode = 'which_music'

/** The closed options of `which_music`, in the backend's order; the page phrases them. */
export const WHICH_MUSIC_OPTIONS: readonly string[] = ['my_playlists', 'no_music']

/**
 * A question of the reading (backend `Question`). The model's question has its text and options
 * as plain text. A closed one has a `code`, an empty `text` and its options as codes: the page
 * phrases it from the catalogue (`lib/readingQuestionWords.ts`), never from the model.
 */
export interface ReadingQuestion {
  topic: QuestionTopic
  code?: QuestionCode
  text: string
  options: string[]
  /** The option the app assumes; null for a required question (a music question with no hint of what to play: nothing is assumed). */
  assumedIndex: number | null
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

function offersNoMusic(count: number, assumed: unknown, marker: unknown): boolean {
  return typeof marker === 'number' && Number.isInteger(marker) && marker >= 0 && marker < count && marker !== assumed
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
    const { topic, code, text, options, assumed_index: assumedIndex, no_music_index: noMusicIndex } = entry
    if (code !== undefined && code !== null) {
      // A closed question is exactly the backend's: anything else is left out, never shown.
      const closed =
        code === 'which_music' &&
        topic === 'music' &&
        (text === undefined || text === null) &&
        (assumedIndex === undefined || assumedIndex === null) &&
        Array.isArray(options) &&
        options.length === WHICH_MUSIC_OPTIONS.length &&
        options.every((o, n) => o === WHICH_MUSIC_OPTIONS[n])
      if (closed) questions.push({ topic: 'music', code: 'which_music', text: '', options: [...WHICH_MUSIC_OPTIONS], assumedIndex: null })
      continue
    }
    if (typeof topic !== 'string' || !TOPICS.includes(topic)) continue
    if (typeof text !== 'string' || text === '') continue
    if (!Array.isArray(options) || options.length < MIN_OPTIONS || options.length > MAX_OPTIONS) continue
    if (!options.every((o): o is string => typeof o === 'string' && o !== '')) continue
    // "No music" is always offered (Amendment 37 decision 6): a music question marks it, in range
    // and not the assumed answer, or it is left out (the backend stores no other).
    if (topic === 'music' && !offersNoMusic(options.length, assumedIndex, noMusicIndex)) continue
    if (assumedIndex === undefined || assumedIndex === null) {
      if (topic !== 'music') continue // only a music question goes without an assumed answer
      questions.push({ topic, text, options, assumedIndex: null })
      continue
    }
    if (typeof assumedIndex !== 'number' || !Number.isInteger(assumedIndex) || assumedIndex < 0 || assumedIndex >= options.length) continue
    questions.push({ topic: topic as QuestionTopic, text, options, assumedIndex })
  }
  return { pinnedSongs, questions }
}
