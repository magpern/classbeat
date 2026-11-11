import { useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { visibleSiteLinks } from '../../publicSite/siteLinks'

/** Links to the public pages; each shows only once its path is published (ADR 0030 section 2). */
export function SiteLinksNav({ className, published }: { className?: string; published?: readonly string[] }) {
  const t = useT()
  const links = visibleSiteLinks(published)
  if (links.length === 0) return null
  return (
    <nav aria-label={t('pub.nav.label')} className={className}>
      {links.map((link) => (
        <Link key={link.path} to={link.path} className="inline-flex min-h-11 items-center">
          {t(link.label)}
        </Link>
      ))}
    </nav>
  )
}
