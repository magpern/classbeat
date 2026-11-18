import type { ReadingQuestion } from '../../api/readingExtras'

// The quick answers of a reading question (ADR 0038 section 3): each sends a prepared sentence as
// an ordinary chat change. A value equal to the assumed one is not offered as a change (it would
// spend one of the class's revises on nothing); "keep it" is a local dismissal instead.

export type QuickAnswer =
  | { kind: 'length'; minutes: number }
  | { kind: 'effort'; effort: 'easy' | 'moderate' | 'hard' }

const LENGTHS = [30, 45, 60] as const
const EFFORTS = ['easy', 'moderate', 'hard'] as const

export function quickAnswers(question: ReadingQuestion): QuickAnswer[] {
  if (question.question === 'how_long') {
    return LENGTHS.filter((m) => m !== question.assumed).map((minutes) => ({ kind: 'length', minutes }))
  }
  return EFFORTS.filter((e) => e !== question.assumed).map((effort) => ({ kind: 'effort', effort }))
}
