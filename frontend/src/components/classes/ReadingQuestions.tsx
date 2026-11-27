import { useId, useState } from 'react'
import { MY_PLAYLISTS_INDEX } from '../../api/readingExtras'
import { useComposerFlag } from '../../hooks/composer/useComposerFlag'
import type { ReadingQuestion } from '../../api/readingExtras'
import type { Confirmation } from '../../hooks/useHandoff'
import { useT } from '../../i18n/I18n'
import {
  MAX_ANSWER,
  MAX_NOTE,
  asksMusic,
  changed,
  choosesPlaylists,
  incomplete,
  initialAnswers,
  rereadBody,
  type ConfirmAnswers,
} from '../../lib/readingAnswers'
import { optionLabel, questionText } from '../../lib/readingQuestionWords'
import { rereadErrorText } from '../../lib/rereadError'
import { Notice } from '../forms'
import { TextInput } from '../newclass/inputs'
import { Button } from '../ui/button'
import { ReadingPlaylistPicker } from './ReadingPlaylistPicker'

function QuestionBlock({
  question,
  index,
  answers,
  onChange,
  disabled,
  playlists,
}: {
  /** Playlists can be shared with a re-read (the composer is available to the user). */
  playlists: boolean
  question: ReadingQuestion
  index: number
  answers: ConfirmAnswers
  onChange: (next: ConfirmAnswers) => void
  disabled: boolean
}) {
  const t = useT()
  const name = useId()
  const a = answers.questions[index] ?? { option: null, text: '' }
  const required = question.assumedIndex === null
  const set = (next: { option: number | null; text: string }) => {
    onChange({ ...answers, questions: answers.questions.map((was, i) => (i === index ? next : was)) })
  }
  return (
    <fieldset className="m-0 flex min-w-0 flex-col gap-2 border-0 p-0" data-testid="reading-question">
      <legend className="p-0 font-medium">{questionText(question, t)}</legend>
      {required ? <p className="m-0 text-[0.875rem] text-muted">{t('ws.refused.music_unanswered')}</p> : null}
      <div className="flex flex-col gap-1">
        {question.options.map((_, option) => {
          // The closed "Use one of my playlists" needs the composer (the server refuses attached playlists
          // without it, `feature_off`): not offered in the classic flow, which keeps its other options.
          if (question.code === 'which_music' && option === MY_PLAYLISTS_INDEX && !playlists) return null
          const label = optionLabel(question, option, t)
          const id = `${name}-${String(option)}`
          return (
            <label key={option} htmlFor={id} className="flex min-h-11 cursor-pointer items-center gap-2">
              <input
                id={id}
                type="radio"
                name={name}
                disabled={disabled}
                checked={a.option === option}
                onChange={() => {
                  set({ option, text: '' })
                }}
              />
              <span>{label}</span>
              {question.assumedIndex === option ? <span className="text-sm text-muted">{t('interp.confirm.assumed')}</span> : null}
            </label>
          )
        })}
      </div>
      {required ? (
        <TextInput
          label={t('interp.confirm.typed')}
          maxLength={MAX_ANSWER}
          disabled={disabled}
          value={a.text}
          onChange={(e) => {
            set({ option: null, text: e.target.value })
          }}
        />
      ) : null}
      {choosesPlaylists(question, a) ? (
        <ReadingPlaylistPicker
          tags={answers.playlists}
          onChange={(playlists) => {
            onChange({ ...answers, playlists })
          }}
        />
      ) : null}
    </fieldset>
  )
}

/**
 * The reading's questions on the confirm screen (#1538), shared by the classic workspace
 * (`ReadingExtras`) and the composer's chat (`ReadingCard`). Each question shows its options with
 * the assumed one pre-selected and marked; a required one (a music question with no hint of what
 * to play) is empty and keeps both buttons off until it is answered. "Anything to correct?" takes
 * free words. With nothing changed **Design this class** agrees to the reading and the design
 * starts; with a changed answer or typed words **Update the reading** sends them and the request
 * is read again (no class credit). A reading with no music question still offers "No music", and
 * the closed "Use one of my playlists" answer shows a playlist picker. Nothing is sent before.
 */
export function ReadingQuestions({ questions, confirm }: { questions: readonly ReadingQuestion[]; confirm: Confirmation }) {
  const t = useT()
  const [answers, setAnswers] = useState<ConfirmAnswers>(() => initialAnswers(questions))
  // The same answer the server's re-read admit uses (`composer_available`): `GET /api/connections`' `composer_flow`.
  const playlists = useComposerFlag(true) === true
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const dirty = changed(questions, answers)
  const blocked = incomplete(questions, answers)
  const update = () => {
    setSending(true)
    setError(null)
    void confirm.onReread(rereadBody(confirm.interpretJobId, questions, answers)).then((refused) => {
      setSending(false)
      if (refused !== null) setError(rereadErrorText(t, refused))
    })
  }
  return (
    <form
      aria-label={t('interp.confirm.title')}
      data-testid="reading-confirm"
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault()
      }}
    >
      <p className="m-0 text-[0.9375rem]">{t('interp.confirm.intro')}</p>
      {confirm.rereadFailed ? <Notice kind="error">{t('interp.confirm.rereadFailed')}</Notice> : null}
      {questions.map((question, index) => (
        <QuestionBlock
          key={`${String(index)}-${question.topic}-${question.code ?? question.text}`}
          question={question}
          index={index}
          answers={answers}
          disabled={sending}
          playlists={playlists}
          onChange={setAnswers}
        />
      ))}
      {!asksMusic(questions) ? (
        <label className="flex min-h-11 cursor-pointer items-center gap-2">
          <input
            type="checkbox"
            disabled={sending}
            checked={answers.noMusic}
            onChange={(e) => {
              setAnswers({ ...answers, noMusic: e.target.checked })
            }}
          />
          <span>{t('interp.confirm.noMusic')}</span>
        </label>
      ) : null}
      <TextInput
        label={t('interp.confirm.correct')}
        maxLength={MAX_NOTE}
        disabled={sending}
        value={answers.note}
        onChange={(e) => {
          setAnswers({ ...answers, note: e.target.value })
        }}
      />
      {error !== null ? <Notice kind="error">{error}</Notice> : null}
      <div className="flex flex-wrap gap-2">
        <Button disabled={sending || dirty || blocked} onClick={confirm.onDesign}>
          {t('interp.confirm.design')}
        </Button>
        <Button variant="secondary" disabled={sending || !dirty || blocked} onClick={update}>
          {t('interp.confirm.update')}
        </Button>
      </div>
    </form>
  )
}
