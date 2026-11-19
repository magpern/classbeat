import type { ReactNode } from 'react'
import { cn } from '../../lib/cn'

/** The small tracked-capitals label (an "eyebrow", ADR 0013 Afterburn A1). */
export const EYEBROW = 'm-0 text-xs font-semibold uppercase tracking-[0.14em] text-muted'

/** A card of the start screens under the brief: an eyebrow label over its content (Length, Music from). */
export function FieldCard({ title, className, children }: { title: string; className?: string; children: ReactNode }) {
  return (
    <section className={cn('flex min-w-0 flex-col gap-3 rounded-3xl border border-line bg-surface p-4 shadow-card', className)}>
      <h2 className={EYEBROW}>{title}</h2>
      {children}
    </section>
  )
}
