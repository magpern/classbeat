import { Plus } from 'lucide-react'
import { useT } from '../../i18n/I18n'
import { PRESETS, presetLink } from '../../lib/presets'
import { Link } from '../../router/Router'
import { Button } from '../ui/button'

/**
 * Home's hero card (ADR 0013, Afterburn A3): a 28 px card with the heat line on top, the quick
 * starts as chips and the one "Design a class" action. The top bar already has New class from
 * 640 px, so there the button is the outlined heat variant; on a phone it is the primary.
 */
export function HomeHero() {
  const t = useT()
  return (
    <section
      aria-label={t('home.design.button')}
      className="relative flex flex-col gap-5 overflow-hidden rounded-xl border border-line bg-surface p-6 shadow-card sm:p-8"
    >
      <div aria-hidden="true" className="absolute inset-x-0 top-0 h-1 bg-heat" />
      <ul className="m-0 flex list-none flex-wrap gap-2 p-0" aria-label={t('home.quick')}>
        {PRESETS.map((preset) => (
          <li key={preset.label}>
            <Link
              to={presetLink(preset)}
              className="chip-lift inline-flex min-h-11 items-center rounded-full border border-line bg-raised px-4 font-medium text-text no-underline hover:bg-line"
            >
              {t(preset.label)}
            </Link>
          </li>
        ))}
      </ul>
      <Button
        asChild
        className="min-h-13 w-full px-7 text-base font-bold sm:w-fit sm:border-2 sm:border-heat sm:text-text sm:shadow-none"
      >
        <Link to="/classes/new">
          <Plus aria-hidden="true" size={18} strokeWidth={2.5} />
          {t('home.design.button')}
        </Link>
      </Button>
    </section>
  )
}
