import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type AnchorHTMLAttributes,
  type MouseEvent,
  type ReactNode,
} from 'react'
import { useLocale } from '../i18n/I18n'
import { localeFromPath, type Locale } from '../i18n/locales'
import { isAdminPath, localize, neutralize, splitPath, withLanguage } from './paths'

// A tiny history-API router: the app has a handful of flat routes and no need for a library.

export interface Location {
  pathname: string
  search: string
}

interface NavigateOptions {
  replace?: boolean
  /** Scroll to the top after navigating (default). Redirects and language switches keep the place. */
  scroll?: boolean
  /** The prefix to add instead of the current URL's (sign-in lands in the account's language). */
  lang?: Locale
}

interface RouterValue extends Location {
  /** The language of the URL's prefix, or null (unprefixed, such as `/admin`). */
  lang: Locale | null
  /** The path without the prefix and the trailing slash: what routes match and `return_to` carries. */
  path: string
  /** Takes a language-neutral target; the prefix is added here (ADR 0027 section 8). */
  navigate: (to: string, options?: NavigateOptions) => void
  /** The same page in another language, keeping search and fragment. Writes nothing. */
  switchLanguage: (lang: Locale) => void
}

const RouterContext = createContext<RouterValue | null>(null)

function readLocation(): Location {
  if (typeof window === 'undefined') return { pathname: '/', search: '' }
  return { pathname: window.location.pathname, search: window.location.search }
}

/** `initialLocation` lets the build-time prerender render a page for a path without a window. */
export function RouterProvider({
  children,
  initialLocation,
}: {
  children: ReactNode
  initialLocation?: Location
}) {
  const [location, setLocation] = useState<Location>(() => initialLocation ?? readLocation())
  const { locale } = useLocale()
  // The language to prefix with when the URL names none (admin pages).
  const fallback = useRef(locale)
  useEffect(() => {
    fallback.current = locale
  }, [locale])

  useEffect(() => {
    const onPop = () => {
      setLocation(readLocation())
    }
    window.addEventListener('popstate', onPop)
    return () => {
      window.removeEventListener('popstate', onPop)
    }
  }, [])

  const navigate = useCallback((to: string, options?: NavigateOptions) => {
    const target = localize(to, options?.lang ?? localeFromPath(window.location.pathname) ?? fallback.current)
    if (options?.replace) window.history.replaceState(null, '', target)
    else window.history.pushState(null, '', target)
    setLocation(readLocation())
    if (options?.scroll !== false) window.scrollTo?.(0, 0)
  }, [])

  const switchLanguage = useCallback(
    (lang: Locale) => {
      const { path } = splitPath(window.location.pathname)
      // The admin area has no language in its URL (ADR 0027 section 1).
      if (isAdminPath(path)) return
      const { search, hash } = window.location
      navigate(`${withLanguage(lang, path)}${search}${hash}`, { replace: true, scroll: false })
    },
    [navigate],
  )

  const value = useMemo(() => {
    const { lang, path } = splitPath(location.pathname)
    return { ...location, lang, path, navigate, switchLanguage }
  }, [location, navigate, switchLanguage])
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>
}

export function useRouter(): RouterValue {
  const value = useContext(RouterContext)
  if (value === null) throw new Error('useRouter needs a RouterProvider')
  return value
}

export function useQuery(): URLSearchParams {
  const { search } = useRouter()
  return useMemo(() => new URLSearchParams(search), [search])
}

interface LinkProps extends Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> {
  to: string
}

export function Link({ to, onClick, children, ...rest }: LinkProps) {
  const { navigate, lang } = useRouter()
  const { locale } = useLocale()
  const handle = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event)
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    ) {
      return
    }
    event.preventDefault()
    navigate(to)
  }
  return (
    <a {...rest} href={localize(to, lang ?? locale)} onClick={handle}>
      {children}
    </a>
  )
}

/** `return_to` is honoured only as a relative path (ADR 0003 section 4). */
export function safeReturnTo(value: string | null): string | null {
  if (value === null) return null
  // Language-neutral whatever came in (the prefix is added again when it is followed, ADR 0027
  // section 4). Neutralizing changes the value, so the checks run on what is returned.
  const neutral = neutralize(value)
  if (!isPlainPath(value) || !isPlainPath(neutral)) return null
  // Also refuse what a decoder further along would turn into `//host` or a backslash.
  try {
    if (!isPlainPath(decodeURIComponent(value)) || !isPlainPath(decodeURIComponent(neutral))) return null
  } catch {
    return null
  }
  return neutral
}

function isPlainPath(value: string): boolean {
  if (!value.startsWith('/') || value.startsWith('//')) return false
  if (value.includes('\\') || value.includes('://')) return false
  for (const char of value) {
    if (char.charCodeAt(0) < 0x20 || char.charCodeAt(0) === 0x7f) return false
  }
  return true
}

/** Changes only the fragment of the current URL (a tab choice); the prefix and search stay as they are. */
export function replaceHash(hash: string): void {
  window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}#${hash}`)
}

/**
 * The `token` of a mail link (`/reset#token=...`). Reading is pure; `stripFragment` removes the
 * token from the address bar and history so it cannot leak through screenshots or sharing.
 */
export function readFragmentToken(): string | null {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ''))
  return params.get('token')
}

export function stripFragment(): void {
  if (window.location.hash !== '') {
    window.history.replaceState(null, '', window.location.pathname + window.location.search)
  }
}

/** For mail-link pages: the token from the fragment, removed from the URL on mount. */
export function useMailToken(): string | null {
  const [token, setToken] = useState(readFragmentToken)
  useEffect(() => {
    stripFragment()
    // The same tab may be pointed at a mail link without a reload (a fragment-only change).
    const onHashChange = () => {
      const next = readFragmentToken()
      if (next !== null) setToken(next)
      stripFragment()
    }
    window.addEventListener('hashchange', onHashChange)
    return () => {
      window.removeEventListener('hashchange', onHashChange)
    }
  }, [])
  return token
}
