import type { ReactNode } from 'react'
import { useT } from '../../../i18n/I18n'
import { Chip } from '../../ui/chip'

/**
 * One line of the Connections list (ADR 0028 section 3): a mark, the name, an "Optional" chip where
 * it is, one line of status, and the one button that fits (Connect or Disconnect). Presentational:
 * what the buttons do is the list's business.
 */
export function ConnectionRow({
  mark,
  name,
  optional,
  status,
  action,
  testId,
}: {
  mark: string
  name: string
  optional: boolean
  status: ReactNode
  action: ReactNode
  testId: string
}) {
  const t = useT()
  return (
    <li className="flex items-center gap-4 px-5 py-4" data-testid={testId}>
      <span
        aria-hidden="true"
        className="inline-flex size-11 shrink-0 items-center justify-center rounded-md bg-raised font-display text-base font-bold text-text"
      >
        {mark}
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="flex flex-wrap items-center gap-2 font-semibold text-text">
          {name}
          {optional ? <Chip className="py-0 text-xs text-muted">{t('conn.optional')}</Chip> : null}
        </span>
        <span className="text-[0.9375rem] text-muted">{status}</span>
      </div>
      {action}
    </li>
  )
}
