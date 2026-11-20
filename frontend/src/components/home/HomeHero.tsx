import { Plus } from 'lucide-react'
import { useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { Button } from '../ui/button'

/**
 * Home's hero card (ADR 0013, Afterburn A3): a 28 px card with the heat line on top and the one
 * "Design a class" action. The top bar already has New class from 640 px, so there the button is
 * the outlined heat variant; on a phone it is the primary.
 */
export function HomeHero() {
  const t = useT()
  return (
    <section
      aria-label={t('home.design.button')}
      className="relative flex flex-col gap-5 overflow-hidden rounded-xl border border-line bg-surface p-6 shadow-card sm:p-8"
    >
      <div aria-hidden="true" className="absolute inset-x-0 top-0 h-1 bg-heat" />
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
