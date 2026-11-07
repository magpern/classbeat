import { useLocale, useT } from '../i18n/I18n'
import { LOCALES, bestLocale, type Locale } from '../i18n/locales'
import { useRouter } from '../router/Router'

const NAMES: Readonly<Record<string, string>> = { en: 'English', sv: 'Svenska', da: 'Dansk' }

/** The languages to show: what the server offers and this build has, in the manifest's order. */
export function offeredLanguages(offered: readonly string[]): string[] {
  return LOCALES.filter((code) => offered.includes(code))
}

export function languageName(code: string): string {
  return Object.hasOwn(NAMES, code) ? (NAMES[code] ?? code) : code
}

/**
 * What choosing a language does (ADR 0027 section 2): save it where the caller says, then go to the
 * same page under the new prefix. `null` follows the browser again, among the languages the server
 * offers. The picker is the only writer.
 */
export function useChooseLanguage(offered: readonly string[]): (choice: Locale | null) => void {
  const { setLocale } = useLocale()
  const { switchLanguage } = useRouter()
  return (choice) => {
    setLocale(choice)
    switchLanguage(choice ?? bestLocale(offered, null))
  }
}

/** The logged-out picker: saves the choice in this browser only (`cb-locale`). Hidden for one language. */
export function LanguagePicker({ offered }: { offered: readonly string[] }) {
  const { locale } = useLocale()
  const { lang } = useRouter()
  const choose = useChooseLanguage(offered)
  // The URL's language is the one on show, even while its catalogue is still loading.
  const shown = lang ?? locale
  const t = useT()
  const codes = offeredLanguages(offered)
  if (codes.length < 2) return null
  return (
    <select
      aria-label={t('language.label')}
      value={codes.includes(shown) ? shown : 'en'}
      onChange={(event) => {
        choose(event.target.value)
      }}
      className="min-h-11 rounded-md border border-line bg-raised px-3 text-[0.9375rem] text-text"
    >
      {codes.map((code) => (
        <option key={code} value={code}>
          {languageName(code)}
        </option>
      ))}
    </select>
  )
}
