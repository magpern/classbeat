import {
  createContext,
  Fragment,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import type { CatalogueKey } from './en'
import {
  clearStoredLocale,
  DEFAULT_LOCALE,
  isLocale,
  localeFromPath,
  resolveBrowserLocale,
  setActiveLocale,
  storeLocale,
  type Locale,
} from './locales'
import { loadCatalogue, translate, type Params, type TFunction } from './t'

interface I18n {
  locale: Locale
  t: TFunction
  /** A language, or `null` to follow the browser again (forgets the saved choice). */
  setLocale: (locale: Locale | null) => void
  /** Shows a language without saving it anywhere: the URL's prefix decides the view (ADR 0027 section 2). */
  showLocale: (locale: Locale) => void
}

const fallback: I18n = {
  locale: DEFAULT_LOCALE,
  t: (key, params) => translate(DEFAULT_LOCALE, key, params),
  setLocale: () => undefined,
  showLocale: () => undefined,
}

const Context = createContext<I18n>(fallback)

/**
 * Provides the locale and `t`. It starts in `initial` (the browser's choice), loads that catalogue
 * and re-renders once it is there; until then, and for any missing key, the text is English.
 */
export function I18nProvider({ children, initial }: { children: ReactNode; initial?: Locale }) {
  const [wanted, setWanted] = useState<Locale>(
    () => initial ?? localeFromPath(window.location.pathname) ?? resolveBrowserLocale(),
  )
  const [ready, setReady] = useState<Locale>(DEFAULT_LOCALE)

  useEffect(() => {
    let current = true
    void loadCatalogue(wanted).then((ok) => {
      if (!current || !ok) return
      setActiveLocale(wanted)
      document.documentElement.setAttribute('lang', wanted)
      setReady(wanted)
    })
    return () => {
      current = false
    }
  }, [wanted])

  const setLocale = useCallback((locale: Locale | null) => {
    if (locale === null) {
      clearStoredLocale()
      setWanted(resolveBrowserLocale())
    } else if (isLocale(locale)) {
      storeLocale(locale)
      setWanted(locale)
    }
  }, [])

  const showLocale = useCallback((locale: Locale) => {
    if (isLocale(locale)) setWanted(locale)
  }, [])

  const value = useMemo<I18n>(
    () => ({
      locale: ready,
      t: (key, params) => translate(ready, key, params),
      setLocale,
      showLocale,
    }),
    [ready, setLocale, showLocale],
  )
  return <Context value={value}>{children}</Context>
}

export function useT(): TFunction {
  return useContext(Context).t
}

export function useLocale(): Pick<I18n, 'locale' | 'setLocale' | 'showLocale'> {
  const { locale, setLocale, showLocale } = useContext(Context)
  return { locale, setLocale, showLocale }
}

/**
 * A sentence with named slots for links and emphasis: the catalogue text holds `{name}` and no
 * markup; each slot is a React node. A slot the text does not name is dropped; a name with no slot
 * stays as written.
 */
export function Trans({
  id,
  params,
  slots,
}: {
  id: CatalogueKey
  params?: Params
  slots: Readonly<Record<string, ReactNode>>
}) {
  const t = useT()
  const marker = '\u0000'
  const text = t(id, {
    ...params,
    ...Object.fromEntries(
      Object.keys(slots).map((name) => [name, `${marker}${name}${marker}`] as const),
    ),
  })
  const parts = text.split(marker)
  return (
    <>
      {parts.map((part, index) =>
        index % 2 === 1 && Object.hasOwn(slots, part) ? (
          <Fragment key={index}>{slots[part]}</Fragment>
        ) : (
          <Fragment key={index}>{part}</Fragment>
        ),
      )}
    </>
  )
}
