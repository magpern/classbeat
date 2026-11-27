import type { ReadingQuestion } from '../api/readingExtras'
import type { CatalogueKey } from '../i18n/en'
import type { TFunction } from '../i18n/t'

// The words of a reading question (#1536). The model's question is shown in its own words, as
// plain text. A closed question (`code`, backend `reading_question_policy.WHICH_MUSIC` and
// `WHICH_SPORT`, #1544) has no model words: its text and options come from the catalogue, in the
// page's language.

const CLOSED_TEXT = {
  which_music: 'interp.question.whichMusic',
  which_sport: 'interp.question.whichSport',
} as const

const CLOSED_OPTIONS = {
  which_music: {
    my_playlists: 'interp.question.whichMusic.myPlaylists',
    no_music: 'interp.question.whichMusic.noMusic',
  },
  which_sport: {
    cycling: 'newclass.sport.cycling',
    running: 'newclass.sport.running',
  },
} as const

/** The question's sentence. */
export function questionText(question: ReadingQuestion, t: TFunction): string {
  return question.code === undefined ? question.text : t(CLOSED_TEXT[question.code])
}

/** The label of option `index` ('' for no such option). */
export function optionLabel(question: ReadingQuestion, index: number, t: TFunction): string {
  const option = question.options[index] ?? ''
  if (question.code === undefined) return option
  const words: Readonly<Record<string, CatalogueKey>> = CLOSED_OPTIONS[question.code]
  const key = Object.hasOwn(words, option) ? words[option] : undefined
  return key === undefined ? '' : t(key)
}
