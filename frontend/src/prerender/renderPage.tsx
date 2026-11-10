import { renderToString } from 'react-dom/server'
import { App } from '../App'
import type { CatalogueKey } from '../i18n/en'
import { I18nProvider } from '../i18n/I18n'
import { setActiveLocale, type Locale } from '../i18n/locales'
import { loadCatalogue, recordKeys, translate } from '../i18n/t'
import { metaKeys } from '../publicSite/pageMeta'

export interface PrerenderedPage {
  title: string
  description: string
  /** The markup of `#root`: the whole app in its signed-out, session-loading state. */
  body: string
  /** Every catalogue key the page rendered, sorted (PP8 compares them with `untranslated.json`). */
  keys: CatalogueKey[]
}

/** One language and path rendered to HTML. A throw here fails the build. */
export async function renderPage(lang: Locale, path: string): Promise<PrerenderedPage> {
  if (!(await loadCatalogue(lang))) throw new Error(`the ${lang} catalogue did not load`)
  setActiveLocale(lang)
  const keys = new Set<CatalogueKey>()
  recordKeys(keys)
  try {
    const meta = metaKeys(path)
    const body = renderToString(
      <I18nProvider initial={lang}>
        <App initialLocation={{ pathname: `/${lang}${path}`, search: '' }} />
      </I18nProvider>,
    )
    return {
      title: translate(lang, meta.title),
      description: translate(lang, meta.description),
      body,
      keys: [...keys].sort(),
    }
  } finally {
    recordKeys(null)
  }
}
