import { useLocale, useT } from '../i18n/I18n'
import { LOCALES } from '../i18n/locales'

const NAMES: Readonly<Record<string, string>> = { en: 'English', sv: 'Svenska', da: 'Dansk' }

/** The languages to show: what the server offers and this build has, in the manifest's order. */
export function offeredLanguages(offered: readonly string[]): string[] {
  return LOCALES.filter((code) => offered.includes(code))
}

export function languageName(code: string): string {
  return Object.hasOwn(NAMES, code) ? (NAMES[code] ?? code) : code
}

/** The logged-out picker: saves the choice in this browser only (`cb-locale`). Hidden for one language. */
export function LanguagePicker({ offered }: { offered: readonly string[] }) {
  const { locale, setLocale } = useLocale()
  const t = useT()
  const codes = offeredLanguages(offered)
  if (codes.length < 2) return null
  return (
    <select
      aria-label={t('language.label')}
      value={codes.includes(locale) ? locale : 'en'}
      onChange={(event) => {
        setLocale(event.target.value)
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
