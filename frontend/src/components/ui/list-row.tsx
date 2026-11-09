import type { HTMLAttributes, ReactNode } from 'react'
import { cn } from '../../lib/cn'

/**
 * The one list row (ADR 0028 section 2): a leading mark, a title with an optional badge, one line
 * of description and one trailing action. Presentational; put rows in a `<ul>`. What the action does
 * is the caller's business.
 */
export function ListRow({
  leading,
  title,
  badge,
  description,
  action,
  className,
  ...props
}: {
  leading?: ReactNode
  title: ReactNode
  /** A small status label beside the title, for example the "Optional" chip. */
  badge?: ReactNode
  description?: ReactNode
  action?: ReactNode
} & Omit<HTMLAttributes<HTMLLIElement>, 'title'>) {
  return (
    <li className={cn('flex items-center gap-4 px-5 py-4', className)} {...props}>
      {leading}
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="flex flex-wrap items-center gap-2 font-semibold text-text">
          {title}
          {badge}
        </span>
        {description ? <span className="text-[0.9375rem] text-muted">{description}</span> : null}
      </div>
      {action}
    </li>
  )
}
