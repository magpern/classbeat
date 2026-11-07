import { useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'

/** Privacy and Terms links at the foot of every page, signed in or out. */
export function LegalFooter() {
  const t = useT()
  return (
    <nav
      aria-label={t('legal.footer.label')}
      className="mt-12 flex flex-wrap gap-x-6 border-t border-line pt-4 text-[0.9375rem]"
    >
      <Link to="/privacy" className="inline-flex min-h-11 items-center">
        {t('legal.link.privacy')}
      </Link>
      <Link to="/terms" className="inline-flex min-h-11 items-center">
        {t('legal.link.terms')}
      </Link>
    </nav>
  )
}
