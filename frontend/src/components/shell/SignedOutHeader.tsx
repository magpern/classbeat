import { useT } from '../../i18n/I18n'
import { LOCALES } from '../../i18n/locales'
import { Link } from '../../router/Router'
import { LanguagePicker } from '../LanguagePicker'
import { BrandLink } from './BrandLink'
import { SiteLinksNav } from './SiteLinksNav'

export function SignedOutHeader({ ready, offered }: { ready: boolean; offered: readonly string[] }) {
  const t = useT()
  return (
    <header className="border-b border-line bg-surface">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2">
        <BrandLink />
        {/* On a phone the links take their own row under the brand and the account controls, so the
            placeholder's width (the language picker's real width differs by language) cannot change
            how the row above wraps when the real nav arrives. */}
        <SiteLinksNav className="order-last flex basis-full flex-wrap gap-x-4 sm:order-none sm:basis-auto" />
        {ready ? (
          <nav aria-label={t('shell.nav.account')} className="ml-auto flex flex-wrap items-center justify-end gap-2">
            <LanguagePicker offered={offered} />
            <Link
              to="/signin"
              className="inline-flex min-h-11 items-center rounded-full px-4 font-medium text-text no-underline hover:bg-raised"
            >
              {t('auth.signIn')}
            </Link>
            <Link
              to="/signup"
              className="inline-flex min-h-11 items-center rounded-full bg-brand px-5 font-medium text-on-brand no-underline hover:opacity-90"
            >
              {t('shell.signUp')}
            </Link>
          </nav>
        ) : (
          <NavPlaceholder />
        )}
      </div>
    </header>
  )
}

/**
 * The signed-out nav's room while the session loads, which is also how the prerender writes it
 * (ADR 0030): the same row of controls at the same height, nothing visible and no link, so the
 * page below does not move when the real nav arrives (the takeover gate). A signed-in person never
 * sees sign-in links flash. The picker and the two buttons are the real ones (or hold their own
 * labels) with `visibility: hidden`, in the page's language and with the same padding, so their width
 * is right in every language without a table of widths.
 */
function NavPlaceholder() {
  const t = useT()
  return (
    <div aria-hidden="true" data-testid="nav-placeholder" className="ml-auto flex flex-wrap items-center justify-end gap-2">
      {/* the real picker, not seen: its width differs by language (Svenska, Dansk), and at 390 px a few
          pixels decide whether the controls wrap, which moves the page when the real nav arrives */}
      <span className="invisible">
        <LanguagePicker offered={LOCALES} />
      </span>
      <span className="invisible inline-flex min-h-11 items-center px-4 font-medium">{t('auth.signIn')}</span>
      <span className="invisible inline-flex min-h-11 items-center px-5 font-medium">{t('shell.signUp')}</span>
    </div>
  )
}
