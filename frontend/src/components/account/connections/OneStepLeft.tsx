import { Link2 } from 'lucide-react'
import { useT } from '../../../i18n/I18n'
import { Button } from '../../ui/button'
import { Progress } from '../../ui/progress'

/**
 * "One step left" (ADR 0013 Afterburn A3): shown on Connections while MOWL or Spotify, the two
 * required connections, is missing. A meter of how many are linked and the action for the first
 * one missing. Presentational.
 */
export function OneStepLeft({
  missing,
  linked,
  onConnect,
}: {
  /** The service to link next: its name. */
  missing: string
  /** Required connections linked, of two. */
  linked: number
  onConnect: () => void
}) {
  const t = useT()
  return (
    <section
      aria-label={t('conn.oneStep.title', { name: missing })}
      data-testid="one-step-left"
      className="flex flex-wrap items-center gap-5 rounded-xl border border-line bg-surface p-5 shadow-card sm:px-6"
    >
      <span
        aria-hidden="true"
        className="inline-flex size-12 shrink-0 items-center justify-center rounded-2xl bg-heat text-on-heat"
      >
        <Link2 size={22} />
      </span>
      <div className="flex min-w-0 flex-[1_1_16rem] flex-col gap-1">
        <p className="m-0 text-lg font-bold">{t('conn.oneStep.title', { name: missing })}</p>
        <p className="m-0 text-[0.9375rem] text-muted">{t('conn.oneStep.body')}</p>
      </div>
      <div className="flex w-full flex-wrap items-center gap-4 sm:w-auto">
        <Progress
          value={(linked / 2) * 100}
          aria-label={t('conn.oneStep.progress', { count: linked })}
          className="h-2 w-full sm:w-36 [&>div]:bg-heat"
        />
        <Button onClick={onConnect}>{t('conn.oneStep.action', { name: missing })}</Button>
      </div>
    </section>
  )
}
