import { useEffect } from 'react'
import { useLocale } from '../i18n/I18n'
import { bestLocale, isLocale } from '../i18n/locales'
import { hasUnknownLanguage, isAdminPath, prefixedTarget, withLanguage } from '../router/paths'
import { useRouter } from '../router/Router'
import { useSession } from './SessionContext'

/**
 * The language of the page and the URL that carries it (ADR 0027). The prefix wins for what the
 * page shows and nothing here writes a setting: only the picker does. The account's language is
 * used for an unprefixed URL (redirected once, after the session answered) and for admin pages.
 */
export function AccountLocale() {
  const { status, session } = useSession()
  const { lang, path, pathname, navigate } = useRouter()
  const { showLocale } = useLocale()
  const saved = session?.user?.locale ?? null
  const offered = session?.locales ?? []
  const account = saved !== null && isLocale(saved) ? saved : null
  const loading = status === 'loading'
  const best = bestLocale(offered, account)
  const offeredKey = offered.join(',')

  useEffect(() => {
    const { search, hash } = window.location
    if (lang !== null) {
      showLocale(lang)
      // `/<lang>/admin` is not a route: the admin area has no language in its URL.
      if (isAdminPath(path)) {
        navigate(`${path}${search}${hash}`, { replace: true, scroll: false })
      } else if (pathname.length > 1 && pathname.endsWith('/')) {
        navigate(`${withLanguage(lang, path)}${search}${hash}`, { replace: true, scroll: false })
      } else if (!loading && offered.length > 0 && !offered.includes(lang)) {
        // Supported but not enabled here: the best offered language, same page.
        navigate(`${withLanguage(best, path)}${search}${hash}`, { replace: true, scroll: false })
      }
      return
    }
    if (isAdminPath(path)) {
      if (account !== null) showLocale(account)
      return
    }
    if (hasUnknownLanguage(pathname)) {
      // The not-found card, in the best language, at the address that was asked for.
      if (!loading) showLocale(best)
      return
    }
    // Unprefixed: wait for the session (it says the account's language), then redirect once. The
    // fragment is copied with the rest, so a mail link's token survives (ADR 0027 section 3).
    if (!loading) navigate(prefixedTarget(best, pathname, search, hash), { replace: true, scroll: false })
    // `offered` is compared by value through `offeredKey`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lang, path, pathname, loading, account, best, offeredKey, showLocale, navigate])
  return null
}
