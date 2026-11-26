import { useState } from 'react'
import type { InterpretationItem } from '../../api/interpretationItems'
import type { ReadingExtras, ReadingQuestion } from '../../api/readingExtras'
import { useT } from '../../i18n/I18n'
import { quickAnswers } from '../../lib/composer/quickAnswers'
import { ReadingExtras as PinnedAndQuestions } from '../classes/ReadingExtras'
import { InterpretationNote } from '../classes/InterpretationNote'

function Question({
  question,
  onAnswer,
  disabled,
}: {
  question: ReadingQuestion
  onAnswer: (sentence: string) => Promise<boolean>
  disabled: boolean
}) {
  const t = useT()
  const [kept, setKept] = useState(false)
  if (kept) return null
  const assumed = question.options[question.assumedIndex] ?? ''
  const keep = t('cw.answer.keep', { answer: assumed })
  const text = `${question.text} ${t('interp.question.assumed', { answer: assumed })}`
  return (
    <li className="m-0 flex flex-col gap-2">
      <span>{text}</span>
      <div role="group" aria-label={t('cw.answer.group')} className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => {
            setKept(true)
          }}
          className="inline-flex min-h-11 cursor-pointer items-center rounded-full border border-brand bg-surface px-4 text-sm font-semibold text-brand"
        >
          {keep}
        </button>
        {quickAnswers(question).map((answer) => (
          <button
            key={answer.index}
            type="button"
            disabled={disabled}
            onClick={() => {
              // The question closes only when the answer was stored; a refusal keeps it open.
              void onAnswer(t('cw.answer.sentence', { text: question.text, answer: answer.label })).then((stored) => {
                if (stored) setKept(true)
              })
            }}
            className="inline-flex min-h-11 cursor-pointer items-center rounded-full border border-line bg-surface px-4 text-sm text-text disabled:cursor-not-allowed disabled:opacity-50"
          >
            {answer.label}
          </button>
        ))}
      </div>
    </li>
  )
}

/**
 * The reading as a compact card (ADR 0038 sections 3 and 10.2): how the request was read, the
 * songs that were pinned, and each question with its quick answers. A quick answer sends its
 * prepared sentence as an ordinary chat change; "keep" only closes the question, so it never
 * spends one of the class's changes on the value that was already used.
 */
export function ReadingCard({
  items,
  extras,
  onAnswer,
  disabled,
}: {
  items: readonly InterpretationItem[] | null
  extras: ReadingExtras
  onAnswer: (sentence: string) => Promise<boolean>
  /** A send runs, a message is pending, or the box is blocked: the answers wait. */
  disabled: boolean
}) {
  const t = useT()
  const read = (items !== null && items.length > 0) || extras.pinnedSongs.length > 0 || extras.questions.length > 0
  if (!read) return null
  return (
    <section
      aria-label={t('cw.reading.title')}
      data-testid="reading-card"
      className="flex max-w-[94%] flex-col gap-3 rounded-2xl rounded-bl-sm bg-raised p-4 text-[0.9375rem]"
    >
      <h2 className="m-0 text-base font-semibold">{t('cw.reading.title')}</h2>
      {items !== null ? <InterpretationNote items={items} /> : null}
      <PinnedAndQuestions extras={{ pinnedSongs: extras.pinnedSongs, questions: [] }} />
      {extras.questions.length > 0 ? (
        <ul className="m-0 flex list-none flex-col gap-3 p-0">
          {extras.questions.map((question) => (
            <Question key={`${question.topic}-${question.text}`} question={question} onAnswer={onAnswer} disabled={disabled} />
          ))}
        </ul>
      ) : null}
    </section>
  )
}
