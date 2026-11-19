import type { ReactNode } from 'react'
import { cn } from '../../lib/cn'

const TITLE_SIZE = {
  page: 'text-4xl sm:text-5xl',
  section: 'text-2xl',
} as const

// A page heading stacks its actions under the title on a phone; a section heading keeps them on the
// same row at every width ("See all" beside its title), with the title free to shrink and wrap.
const LAYOUT = {
  page: { row: 'flex-wrap items-end', actions: 'flex w-full flex-col items-start sm:w-auto sm:items-end' },
  section: { row: 'flex-nowrap items-baseline', actions: 'flex shrink-0 flex-col items-end' },
} as const

/**
 * The Afterburn heading block (ADR 0013, Afterburn A3): an optional eyebrow, the display title and
 * an optional intro, with an optional `actions` slot (right-aligned from 640 px, stacked on a
 * phone). A title alone renders as the bare heading. The title is an `h1` unless `as` says otherwise; `size` picks the page or section scale.
 */
export function PageHeading({
  eyebrow,
  title,
  intro,
  actions,
  size = 'page',
  as: Heading = 'h1',
  titleId,
  titleClassName,
  className,
}: {
  eyebrow?: ReactNode
  title: ReactNode
  intro?: ReactNode
  actions?: ReactNode
  size?: keyof typeof TITLE_SIZE
  as?: 'h1' | 'h2' | 'h3'
  /** The `id` of the heading, for a `section` that is labelled by it. */
  titleId?: string
  titleClassName?: string
  className?: string
}) {
  const heading = (
    <Heading id={titleId} className={cn('m-0 font-display font-extrabold', TITLE_SIZE[size], titleClassName)}>
      {title}
    </Heading>
  )
  // A bare title (a section heading) adds no wrapper, so it stays a direct child of its section.
  if (!eyebrow && !intro && !actions) return heading
  return (
    <div className={cn('flex justify-between gap-4', LAYOUT[size].row, className)}>
      <div className="flex min-w-0 flex-col gap-2">
        {eyebrow ? <p className="m-0 text-xs font-semibold uppercase tracking-[0.14em] text-muted">{eyebrow}</p> : null}
        {heading}
        {intro ? <p className="m-0 max-w-xl text-base text-muted">{intro}</p> : null}
      </div>
      {actions ? <div className={LAYOUT[size].actions}>{actions}</div> : null}
    </div>
  )
}
