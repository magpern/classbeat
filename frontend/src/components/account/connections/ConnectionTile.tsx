import type { ReactNode } from 'react'
import { useT } from '../../../i18n/I18n'
import { cn } from '../../../lib/cn'
import { Chip } from '../../ui/chip'

export type ConnectionState = 'connected' | 'again' | 'none'

const TONE = { connected: 'ok', again: 'warn', none: 'neutral' } as const

/**
 * One service of the Connections tab as a tile (ADR 0013 Afterburn A3): a monogram square, a status
 * pill (Connected, Needs reconnecting, Not connected), the name with a Required or Optional tag,
 * one line of detail (the purpose, or the account) and the one button that fits. Presentational:
 * what the button does is the list's business. It is a list item; put tiles in a `<ul>`.
 */
export function ConnectionTile({
  mark,
  name,
  optional,
  state,
  pill,
  detail,
  action,
  testId,
}: {
  mark: string
  name: string
  optional: boolean
  state: ConnectionState
  /** The status words; the caller may wrap them (a test id). */
  pill: ReactNode
  detail: ReactNode
  action: ReactNode
  testId: string
}) {
  const t = useT()
  return (
    <li
      data-testid={testId}
      className="flex flex-col gap-4 rounded-lg border border-line bg-surface p-5 shadow-card sm:p-6"
    >
      <div className="flex items-start justify-between gap-3">
        <span
          aria-hidden="true"
          className={cn(
            'inline-flex size-14 shrink-0 items-center justify-center rounded-2xl font-display text-xl font-extrabold',
            state === 'connected' ? 'bg-ok-soft text-ok' : 'bg-raised text-text',
          )}
        >
          {mark}
        </span>
        <Chip tone={TONE[state]} dot className="font-semibold">
          {pill}
        </Chip>
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <span className="flex flex-wrap items-center gap-2 text-lg font-bold text-text">
          {name}
          <Chip
            tone={optional ? 'neutral' : 'warn'}
            className={cn('px-2 py-0.5 text-xs font-semibold', optional && 'text-muted')}
          >
            {optional ? t('conn.optional') : t('conn.required')}
          </Chip>
        </span>
        {detail ? <span className="text-[0.9375rem] break-words text-muted">{detail}</span> : null}
      </div>
      {action ? <div className="flex flex-col [&>button]:w-full">{action}</div> : null}
    </li>
  )
}
