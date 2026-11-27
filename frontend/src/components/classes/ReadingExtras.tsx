import type { ReadingExtras as Extras, ReadingQuestion } from '../../api/readingExtras'
import type { Confirmation } from '../../hooks/useHandoff'
import { useT } from '../../i18n/I18n'
import { optionLabel, questionText } from '../../lib/readingQuestionWords'
import { ReadingQuestions } from './ReadingQuestions'

function QuestionLine({ question }: { question: ReadingQuestion }) {
  const t = useT()
  const text = questionText(question, t)
  if (question.assumedIndex === null) return <>{text}</>
  return <>{`${text} ${t('interp.question.assumed', { answer: optionLabel(question, question.assumedIndex, t) })}`}</>
}

/**
 * The composer's part of the reading (ADR 0038 section 3): the songs the instructor named, with
 * where they go, and the follow-up questions with the value that was assumed. Nothing waits for an
 * answer. A song's title, artist and place are the instructor's own words, shown as plain text;
 * every other sentence comes from the catalogue. Nothing shows for a class without them.
 *
 * While the reading waits for the instructor (`confirm`, #1538) the questions are answerable
 * (`ReadingQuestions`): the assumed answers pre-selected, "Design this class" and "Update the
 * reading". The confirm screen shows even when the reading asked nothing.
 */
export function ReadingExtras({ extras, confirm = null }: { extras: Extras; confirm?: Confirmation | null }) {
  const t = useT()
  const { pinnedSongs, questions } = extras
  if (pinnedSongs.length === 0 && questions.length === 0 && confirm === null) return null
  return (
    <div className="flex flex-col gap-3" data-testid="reading-extras">
      {pinnedSongs.length > 0 ? (
        <section aria-label={t('interp.pinned')} className="flex flex-col gap-1">
          <h4 className="m-0 text-sm font-semibold uppercase tracking-wide text-muted">{t('interp.pinned')}</h4>
          <ul className="m-0 flex list-none flex-col gap-1 p-0">
            {pinnedSongs.map((song, n) => (
              <li key={n} className="m-0 break-words">
                {song.artist !== null ? t('interp.pinned.byArtist', { title: song.title, artist: song.artist }) : song.title}
                {song.at !== null ? (
                  <span className="block text-sm text-muted">{t('interp.pinned.at', { at: song.at })}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {confirm !== null ? (
        <ReadingQuestions key={confirm.interpretJobId} questions={questions} confirm={confirm} />
      ) : questions.length > 0 ? (
        <section aria-label={t('interp.questions')} className="flex flex-col gap-1">
          <h4 className="m-0 text-sm font-semibold uppercase tracking-wide text-muted">{t('interp.questions')}</h4>
          <ul className="m-0 flex list-none flex-col gap-1 p-0">
            {questions.map((question) => (
              <li key={`${question.topic}-${question.code ?? question.text}`} className="m-0">
                <QuestionLine question={question} />
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  )
}
