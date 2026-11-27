import type { RereadAnswer, RereadBody } from '../api/interpret'
import { MY_PLAYLISTS_INDEX, type ReadingQuestion } from '../api/readingExtras'
import { NO_TAGS, attachedPlaylists, type MusicTags } from '../components/start/musicTags'

// The instructor's answers on the confirm screen (#1538; pure). Every question starts on the
// answer the app assumed (a required music question has none); the instructor may pick another
// option, type their own words for a required question, add a correction, ask for "No music"
// (when the reading asked no music question that has the option) or pick playlists. With
// nothing changed "Design this class" agrees to the reading; with a change "Update the reading"
// sends the answers and the request is read again.

/** The longest answer and correction the server takes (backend `ANSWER_CHARS`, `NOTE_CHARS`). */
export const MAX_ANSWER = 200
export const MAX_NOTE = 500

export interface QuestionAnswer {
  /** The chosen option; null for none (a required question that is not answered yet). */
  option: number | null
  /** The instructor's own words, for a required question only; exclusive with `option`. */
  text: string
}

export interface ConfirmAnswers {
  questions: readonly QuestionAnswer[]
  /** "Anything to correct?" */
  note: string
  /** "No music" for a reading with no music question to mark it. */
  noMusic: boolean
  /** The playlists of the picker, used with the closed "Use one of my playlists" option. */
  playlists: MusicTags
}

export function initialAnswers(questions: readonly ReadingQuestion[]): ConfirmAnswers {
  return { questions: questions.map((q) => ({ option: q.assumedIndex, text: '' })), note: '', noMusic: false, playlists: NO_TAGS }
}

/** Whether the reading has a music question: it has its own marked "No music" option, so no extra one is offered. */
export const asksMusic = (questions: readonly ReadingQuestion[]): boolean => questions.some((q) => q.topic === 'music')

const typed = (a: QuestionAnswer): string => a.text.trim()

/** A required question is answered by an option or by words. */
const answered = (a: QuestionAnswer): boolean => a.option !== null || typed(a) !== ''

/** Whether the answer to `question` is the closed "Use one of my playlists" option. */
export const choosesPlaylists = (question: ReadingQuestion, a: QuestionAnswer): boolean =>
  question.code === 'which_music' && a.option === MY_PLAYLISTS_INDEX

const picksPlaylists = (questions: readonly ReadingQuestion[], answers: ConfirmAnswers): boolean =>
  questions.some((q, i) => {
    const a = answers.questions[i]
    return a !== undefined && choosesPlaylists(q, a)
  })

/** Something differs from what was assumed: the reading must be read again before it can be designed. */
export function changed(questions: readonly ReadingQuestion[], answers: ConfirmAnswers): boolean {
  return (
    answers.note.trim() !== '' ||
    answers.noMusic ||
    questions.some((q, i) => {
      const a = answers.questions[i]
      return a !== undefined && (a.option !== q.assumedIndex || typed(a) !== '')
    })
  )
}

/**
 * What stops both buttons: a required question with no answer (nothing is picked without a hint of
 * what to play, unless "No music" is chosen), or "Use one of my playlists" with no playlist.
 */
export function incomplete(questions: readonly ReadingQuestion[], answers: ConfirmAnswers): boolean {
  const unanswered = questions.some((q, i) => q.assumedIndex === null && !answered(answers.questions[i] ?? { option: null, text: '' }))
  return unanswered || (picksPlaylists(questions, answers) && attachedPlaylists(answers.playlists).length === 0)
}

/** The body of the re-read: only the answers that differ from what was assumed. */
export function rereadBody(interpretJobId: string, questions: readonly ReadingQuestion[], answers: ConfirmAnswers): RereadBody {
  const sent: RereadAnswer[] = []
  questions.forEach((q, questionIndex) => {
    const a = answers.questions[questionIndex]
    if (a === undefined) return
    if (typed(a) !== '') sent.push({ questionIndex, text: typed(a) })
    else if (a.option !== null && a.option !== q.assumedIndex) sent.push({ questionIndex, optionIndex: a.option })
  })
  const note = answers.note.trim()
  const ids = picksPlaylists(questions, answers) ? attachedPlaylists(answers.playlists).map((p) => p.id) : []
  return {
    interpretJobId,
    answers: sent,
    ...(note !== '' ? { note } : {}),
    ...(answers.noMusic && !asksMusic(questions) ? { noMusic: true as const } : {}),
    ...(ids.length > 0 ? { musicSources: ids } : {}),
  }
}
