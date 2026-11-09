import { ChevronDown } from 'lucide-react'
import type { ReactNode } from 'react'
import { useT } from '../../i18n/I18n'

/** A closed-by-default disclosure for the long part of a notice (ADR 0028: detail goes behind "Details"). */
export function Details({ children, label }: { children: ReactNode; label?: string }) {
  const t = useT()
  return (
    <details className="group">
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-1 font-medium [&::-webkit-details-marker]:hidden">
        {label ?? t('common.details')}
        <ChevronDown aria-hidden="true" size={16} className="text-muted transition-transform group-open:rotate-180" />
      </summary>
      <div className="flex flex-col gap-2">{children}</div>
    </details>
  )
}
