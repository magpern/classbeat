import type { ClassMessage } from '../../api/classes'
import { describeChange } from '../../api/classMessages'
import type { InterpretationItem } from '../../api/interpretationItems'
import type { PendingMessage } from '../../api/pendingMessages'
import { pendingReasonText } from '../../api/pendingReasons'
import type { ReadingExtras } from '../../api/readingExtras'
import { useT } from '../../i18n/I18n'
import { recallRequest } from '../../lib/startRequest'
import { Chip } from '../ui/chip'
import { MessageBox } from './MessageBox'
import { ReadingCard } from './ReadingCard'

function Bubble({ who, mine, children }: { who: string; mine: boolean; children: React.ReactNode }) {
  return (
    <li
      className={`m-0 flex max-w-[94%] flex-col gap-1 rounded-2xl px-4 py-3 text-[0.9375rem] ${
        mine ? 'self-end rounded-br-sm bg-brand text-on-brand' : 'rounded-bl-sm bg-raised'
      }`}
    >
      <span className={`text-xs font-semibold ${mine ? 'opacity-80' : 'text-muted'}`}>{who}</span>
      {children}
    </li>
  )
}

/** The request with its music tags (ADR 0038 section 10.2). The attached lists come from this tab's kept request. */
function Request({ classId, body }: { classId: string; body: string }) {
  const t = useT()
  const lists = recallRequest(classId)?.playlists?.length ?? 0
  return (
    <section aria-label={t('cw.request')} data-testid="request-block" className="flex max-w-[94%] flex-col gap-2 self-end">
      <p className="m-0 whitespace-pre-wrap break-words rounded-2xl rounded-br-sm bg-brand px-4 py-3 text-[0.9375rem] text-on-brand">
        {body}
      </p>
      {lists > 0 ? (
        <ul aria-label={t('cw.request.music')} className="m-0 flex list-none flex-wrap justify-end gap-2 p-0">
          <li className="m-0">
            <Chip>{t('cw.tag.lists', { count: lists })}</Chip>
          </li>
        </ul>
      ) : null}
    </section>
  )
}

/**
 * The chat, left (ADR 0038 section 10.2): the request with its tags, the reading with its
 * questions, the replies, the messages that wait or were refused, and the message box.
 */
export function ChatColumn({
  classId,
  request,
  messages,
  numbers,
  items,
  extras,
  music = null,
  stored,
  send,
  blocked,
  restore,
}: {
  classId: string
  request: ClassMessage | null
  /** The class's messages after the request. */
  messages: readonly ClassMessage[]
  numbers: ReadonlyMap<string, number>
  items: readonly InterpretationItem[] | null
  extras: ReadingExtras
  /** The music question or the way on to the song search (`MusicSetup`), shown under the reading. */
  music?: React.ReactNode
  /** The stored messages: the one that waits, and the newest if it was refused. */
  stored: { pending: PendingMessage | null; failed: PendingMessage | null }
  send: { run: (text: string) => Promise<boolean>; busy: boolean; error: string | null }
  blocked: string | null
  restore: { id: string; text: string } | null
}) {
  const t = useT()
  const { pending, failed } = stored
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <ol aria-label={t('cw.chat.history')} className="m-0 flex list-none flex-col gap-3 p-0">
        {request !== null ? (
          <li className="m-0 flex flex-col">
            <Request classId={classId} body={request.body} />
          </li>
        ) : null}
        <li className="m-0 flex flex-col">
          <ReadingCard items={items} extras={extras} onAnswer={send.run} disabled={send.busy || blocked !== null} />
        </li>
        {music !== null ? <li className="m-0 flex flex-col">{music}</li> : null}
        {messages.map((m) => {
          if (m.role === 'user') {
            return (
              <Bubble key={m.seq} who={t('cw.chat.you')} mine>
                <span className="whitespace-pre-wrap break-words">{m.body}</span>
              </Bubble>
            )
          }
          const lines = m.change === null ? [] : describeChange(m.change)
          const number = m.resultRevisionId === null ? undefined : numbers.get(m.resultRevisionId)
          return (
            <Bubble key={m.seq} who={t('cw.chat.classbeat')} mine={false}>
              {number !== undefined ? <span className="text-sm text-muted">{t('class.chat.changedVersion', { number })}</span> : null}
              {lines.length > 0 ? (
                <ul className="m-0 list-disc pl-5">
                  {lines.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              ) : null}
              {m.body !== '' ? <span className="whitespace-pre-wrap break-words">{m.body}</span> : null}
            </Bubble>
          )
        })}
        {pending !== null ? (
          <Bubble key={pending.messageId} who={t('cw.chat.you')} mine>
            <span className="whitespace-pre-wrap break-words">{pending.body}</span>
            <span className="flex flex-wrap items-center gap-2 text-sm" data-testid="pending-message">
              <Chip tone="warn">{t('cw.chat.pendingTag')}</Chip>
              {t('cw.chat.pending')}
            </span>
          </Bubble>
        ) : null}
        {failed !== null ? (
          <Bubble key={failed.messageId} who={t('cw.chat.you')} mine>
            <span className="whitespace-pre-wrap break-words">{failed.body}</span>
            <span className="flex flex-wrap items-center gap-2 text-sm" data-testid="failed-message">
              <Chip tone="bad">{t('cw.chat.failedTag')}</Chip>
              {t('cw.chat.failed', { reason: pendingReasonText(failed.reason) })}
            </span>
          </Bubble>
        ) : null}
      </ol>
      <MessageBox onSend={send.run} busy={send.busy} blocked={blocked} error={send.error} restore={restore} />
    </div>
  )
}
