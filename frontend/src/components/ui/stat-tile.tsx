import type { HTMLAttributes, ReactNode } from 'react'
import { cn } from '../../lib/cn'

const TONE = {
  raised: 'rounded-2xl bg-raised',
  outlined: 'rounded-lg border border-line bg-surface',
} as const

/** The `<dl>` grid that holds `StatTile`s; give it an `aria-label`. */
export function StatTiles({ className, ...props }: HTMLAttributes<HTMLDListElement>) {
  return <dl className={cn('m-0 grid grid-cols-[repeat(auto-fit,minmax(9rem,1fr))] gap-3', className)} {...props} />
}

/**
 * One Afterburn figure (ADR 0013, Afterburn A3): an eyebrow label over a display value, with an
 * optional sub line, on a raised card. A `dt`/`dd` pair, so it belongs inside `StatTiles`.
 */
export function StatTile({
  label,
  value,
  sub,
  tone = 'raised',
  className,
  ...props
}: {
  label: ReactNode
  value: ReactNode
  sub?: ReactNode
  /** `raised` (default) is the Afterburn card; `outlined` is a bordered surface tile. */
  tone?: 'raised' | 'outlined'
} & Omit<HTMLAttributes<HTMLDivElement>, 'children'>) {
  return (
    <div className={cn('flex min-w-0 flex-col gap-1.5 px-5 py-4', TONE[tone], className)} {...props}>
      <dt className="text-xs font-semibold uppercase tracking-[0.14em] text-muted">{label}</dt>
      <dd className="m-0 font-display text-3xl font-extrabold tabular-nums">{value}</dd>
      {sub ? <dd className="m-0 text-sm text-muted">{sub}</dd> : null}
    </div>
  )
}
