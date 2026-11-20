import { useT } from '../../i18n/I18n'
import { visibleSiteLinks } from '../../publicSite/siteLinks'
import { Link } from '../../router/Router'

/**
 * Privacy and Terms links and the trademark notice at the foot of every page, signed in or out. The
 * notice is the terms' own sentence (`terms.service.p3`, a draft until the owner words it, #1268), so
 * the two places cannot drift apart. The footer is the last thing in the page (the cookie notice
 * spec measures it).
 */
export function LegalFooter({ published }: { published?: readonly string[] }) {
  const t = useT()
  return (
    <footer className="mt-12 border-t border-line pt-4 text-muted">
      <nav aria-label={t('legal.footer.label')} className="flex flex-wrap gap-x-6 text-sm">
        <Link to="/privacy" className="inline-flex min-h-11 items-center text-muted no-underline hover:text-text">
          {t('legal.link.privacy')}
        </Link>{' '}
        <Link to="/terms" className="inline-flex min-h-11 items-center text-muted no-underline hover:text-text">
          {t('legal.link.terms')}
        </Link>{' '}
        <Link to="/help" className="inline-flex min-h-11 items-center text-muted no-underline hover:text-text">
          {t('help.link')}
        </Link>{' '}
        {visibleSiteLinks(published).map((link) => (
          <Link key={link.path} to={link.path} className="inline-flex min-h-11 items-center text-muted no-underline hover:text-text">
            {t(link.label)}
          </Link>
        ))}
      </nav>
      <p className="m-0 mt-2 max-w-prose text-xs leading-relaxed">{t('terms.service.p3')}</p>
    </footer>
  )
}
