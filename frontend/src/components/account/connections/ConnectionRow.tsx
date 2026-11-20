import type { ReactNode } from 'react'
import { useT } from '../../../i18n/I18n'
import { Chip } from '../../ui/chip'
import { ListRow } from '../../ui/list-row'
import { ConnectionMark, type ConnectionMarkContent } from './ConnectionMark'

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
  className,
}: {
  mark: ConnectionMarkContent
  name: string
  optional: boolean
  status: ReactNode
  action: ReactNode
  testId: string
  className?: string
}) {
  const t = useT()
  return (
    <ListRow
      data-testid={testId}
      className={className}
      leading={
        <span
          aria-hidden="true"
          className="inline-flex size-12 shrink-0 items-center justify-center rounded-md bg-raised font-display text-base font-bold text-text"
        >
          <ConnectionMark mark={mark} />
        </span>
      }
      title={name}
      badge={optional ? <Chip className="py-0 text-xs text-muted">{t('conn.optional')}</Chip> : null}
      description={status}
      action={action}
    />
  )
}
