import { useCallback, useEffect, useId, useState } from 'react'
import { getMailPreferences, setMailPreferences, type MailPreferences } from '../../api/mailPrefs'
import { Notice, useAction } from '../forms'
import { Switch } from '../ui/switch'

const KINDS = [
  { id: 'welcome', label: 'Welcome mail' },
  { id: 'account_activity', label: 'Account activity notices' },
  { id: 'class_ready', label: 'A class is ready' },
] as const

/**
 * The Email section: one switch per optional kind and one for all of them. Sign-in links,
 * password resets, security alerts and other account mail are always sent.
 */
export function EmailPreferences() {
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
    return action.error ? <Notice kind="error">{action.error}</Notice> : <p>Loading</p>
  }
  const current = prefs
  const toggle = (id: string, on: boolean) => {
    const rest = current.optedOut.filter((item) => item !== id)
    save({ ...current, optedOut: on ? rest : [...rest, id] })
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="m-0 text-muted">
        Choose which optional mail classbeat sends. Sign-in links, password resets, security alerts and other mail
        about your account are always sent.
      </p>
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
          Optional email
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
          <label htmlFor={`${baseId}-${kind.id}`}>{kind.label}</label>
        </div>
      ))}
    </div>
  )
}
