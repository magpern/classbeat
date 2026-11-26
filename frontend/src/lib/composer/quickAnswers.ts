import type { ReadingQuestion } from '../../api/readingExtras'
import type { TFunction } from '../../i18n/t'
import { optionLabel } from '../readingQuestionWords'

// The quick answers of a reading question (ADR 0038 section 3): one per option other than the
// assumed one (every option of a required question, which has none), each sending a prepared sentence as an ordinary chat change. The assumed option is
// not offered as a change (it would spend one of the class's revises on nothing); "keep it" is a
// local dismissal instead. The model's option text is shown and sent as plain text; a closed
// question's options are phrased from the catalogue (`lib/readingQuestionWords.ts`).

export interface QuickAnswer {
  index: number
  label: string
}

export function quickAnswers(question: ReadingQuestion, t: TFunction): QuickAnswer[] {
  return question.options.flatMap((_, index) =>
    question.assumedIndex !== null && index === question.assumedIndex ? [] : [{ index, label: optionLabel(question, index, t) }],
  )
}
