import type { ReadingQuestion } from '../../api/readingExtras'

// The quick answers of a reading question (ADR 0038 section 3): one per option other than the
// assumed one (every option of a required question, which has none), each sending a prepared sentence as an ordinary chat change. The assumed option is
// not offered as a change (it would spend one of the class's revises on nothing); "keep it" is a
// local dismissal instead. The option text is the model's, shown and sent as plain text.

export interface QuickAnswer {
  index: number
  label: string
}

export function quickAnswers(question: ReadingQuestion): QuickAnswer[] {
  return question.options.flatMap((label, index) => (question.assumedIndex !== null && index === question.assumedIndex ? [] : [{ index, label }]))
}
