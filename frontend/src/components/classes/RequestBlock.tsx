import type { ClassMessage } from '../../api/classes'
import type { InterpretationItem } from '../../api/interpretationItems'
import { NO_EXTRAS, type ReadingExtras as Extras } from '../../api/readingExtras'
import { useT } from '../../i18n/I18n'
import { InterpretationNote } from './InterpretationNote'
import { ReadingExtras } from './ReadingExtras'

/**
 * The start of the chat (MA3): the instructor's request as they wrote it (plain text) and how it
 * was read, with a composer class's pinned songs and questions (ADR 0038 section 3). Nothing
 * shows for a class made on the form, which has no request message.
 */
export function RequestBlock({
  request,
  items,
  extras = NO_EXTRAS,
}: {
  request: ClassMessage | null
  items: readonly InterpretationItem[] | null
  extras?: Extras
}) {
  const t = useT()
  const read = (items !== null && items.length > 0) || extras.pinnedSongs.length > 0 || extras.questions.length > 0
  if (request === null && !read) return null
  return (
    <section aria-labelledby="request-title" className="flex flex-col gap-4" data-testid="request-block">
      <h2 id="request-title" className="m-0 font-display text-xl font-bold">
        {t('ws.request')}
      </h2>
      {request !== null ? (
        <p className="m-0 whitespace-pre-wrap break-words rounded-lg bg-raised p-3">{request.body}</p>
      ) : null}
      {items !== null ? <InterpretationNote items={items} /> : null}
      <ReadingExtras extras={extras} />
    </section>
  )
}
