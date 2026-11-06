import { useId } from 'react'
import { setAccountLocale } from '../../api/locale'
import { useSession } from '../../auth/SessionContext'
import { useLocale, useT } from '../../i18n/I18n'
import { Notice, useAction } from '../forms'
import { languageName, offeredLanguages } from '../LanguagePicker'

const FOLLOW = ''

/** The Account page's language: saved on the account; "Follow this browser" saves nothing. */
export function LanguageSetting() {
  const { session, refresh } = useSession()
  const { setLocale } = useLocale()
  const t = useT()
  const action = useAction()
  const id = useId()
  const codes = offeredLanguages(session?.locales ?? [])
  const saved = session?.user?.locale ?? null
  if (codes.length < 2) return null

  const choose = (value: string) => {
    const next = value === FOLLOW ? null : value
    void action.run(async () => {
      await setAccountLocale(next)
      setLocale(next)
      await refresh()
    })
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="m-0 text-muted">{t('language.help')}</p>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <label htmlFor={id} className="font-medium">
        {t('language.label')}
      </label>
      <select
        id={id}
        value={saved !== null && codes.includes(saved) ? saved : FOLLOW}
        disabled={action.busy}
        onChange={(event) => {
          choose(event.target.value)
        }}
        className="min-h-11 max-w-xs rounded-md border border-line bg-raised px-3 text-[0.9375rem] text-text"
      >
        <option value={FOLLOW}>{t('language.follow')}</option>
        {codes.map((code) => (
          <option key={code} value={code}>
            {languageName(code)}
          </option>
        ))}
      </select>
    </div>
  )
}
