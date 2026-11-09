import { useCallback, useEffect, useId, useState } from 'react'
import { getMailPreferences, setMailPreferences, type MailPreferences } from '../../api/mailPrefs'
import type { CatalogueKey } from '../../i18n/en'
import { useT } from '../../i18n/I18n'
import { Notice, useAction } from '../forms'
import { Switch } from '../ui/switch'

const KINDS = [
  { id: 'welcome', label: 'mailprefs.kind.welcome' },
  { id: 'account_activity', label: 'mailprefs.kind.account_activity' },
  { id: 'class_ready', label: 'mailprefs.kind.class_ready' },
] as const satisfies readonly { id: string; label: CatalogueKey }[]

/**
 * The Email section: one switch per optional kind and one for all of them. Sign-in, password
 * reset and confirmation links are always sent; security notices (failed two-factor attempts, a
 * new sign-in method) are the "Account activity" kind and can be switched off.
 */
export function EmailPreferences() {
  const t = useT()
  const [prefs, setPrefs] = useState<MailPreferences | null>(null)
  const action = useAction()
  const baseId = useId()

  const load = action.run
  useEffect(() => {
    void load(async () => {
      setPrefs(await getMailPreferences())
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load once on mount
  }, [])

  const save = useCallback(
    (next: MailPreferences) => {
      void action.run(async () => {
        setPrefs(await setMailPreferences(next))
      })
    },
    [action],
  )

  if (prefs === null) {
    return action.error ? <Notice kind="error">{action.error}</Notice> : <p>{t('common.loading')}</p>
  }
  const current = prefs
  const toggle = (id: string, on: boolean) => {
    const rest = current.optedOut.filter((item) => item !== id)
    save({ ...current, optedOut: on ? rest : [...rest, id] })
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="m-0 text-muted">{t('mailprefs.intro')}</p>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <div className="flex items-center gap-3">
        <Switch
          id={`${baseId}-all`}
          checked={!current.unsubscribedAll}
          disabled={action.busy}
          onCheckedChange={(on) => {
            save({ ...current, unsubscribedAll: !on })
          }}
        />
        <label htmlFor={`${baseId}-all`} className="font-medium">
          {t('mailprefs.all')}
        </label>
      </div>
      {KINDS.map((kind) => (
        <div key={kind.id} className="flex items-center gap-3">
          <Switch
            id={`${baseId}-${kind.id}`}
            checked={!current.unsubscribedAll && !current.optedOut.includes(kind.id)}
            disabled={action.busy || current.unsubscribedAll}
            onCheckedChange={(on) => {
              toggle(kind.id, on)
            }}
          />
          <label htmlFor={`${baseId}-${kind.id}`}>{t(kind.label)}</label>
        </div>
      ))}
      <p className="m-0 text-muted">{t('mailprefs.securityNote')}</p>
    </div>
  )
}
