import { useCallback, useEffect, useState } from 'react'
import { listPasskeys, removePasskey, renamePasskey, type PasskeyList } from '../../api/passkeys'
import { passkeyErrorMessage, registerPasskey } from '../../auth/passkeyCeremony'
import { useGuard } from '../../auth/prompts'
import { passkeysSupported } from '../../auth/webauthn'
import { useT } from '../../i18n/I18n'
import { Field, Notice, Section, useAction } from '../forms'
import { PasskeyRow } from './PasskeyRow'

/**
 * The Passkeys card (ADR 0019 sections 1 and 5). Without WebAuthn in the browser there is no
 * card at all. With the flag off it shows only when the user already has a passkey, so they can
 * still remove it. Every write runs through the guard (recent auth, and step-up with TOTP).
 */
export function Passkeys() {
  if (!passkeysSupported()) return null
  return <PasskeysCard />
}

function PasskeysCard() {
  const t = useT()
  const guard = useGuard()
  const action = useAction()
  const [state, setState] = useState<PasskeyList | null>(null)
  const [name, setName] = useState('')

  const load = useCallback(async () => {
    setState(await listPasskeys())
  }, [])

  useEffect(() => {
    let current = true
    listPasskeys().then(
      (loaded) => {
        if (current) setState(loaded)
      },
      () => undefined,
    )
    return () => {
      current = false
    }
  }, [])

  if (state === null || (!state.enabled && state.passkeys.length === 0)) return null

  const add = () => {
    void action.run(async () => {
      // One guard around the whole ceremony: after a re-authentication it starts afresh.
      await guard(() => registerPasskey(name.trim() === '' ? undefined : name.trim()))
      setName('')
      await load()
    }, passkeyErrorMessage)
  }
  const rename = (id: string, label: string) => {
    void action.run(async () => {
      await guard(() => renamePasskey(id, label))
      await load()
    })
  }
  const remove = (id: string) => {
    void action.run(async () => {
      await guard(() => removePasskey(id))
      await load()
    })
  }
  const full = state.passkeys.length >= state.max
  return (
    <Section title={t('passkey.title')}>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {state.enabled ? <p>{t('passkey.intro')}</p> : <Notice>{t('passkey.off')}</Notice>}
      {state.passkeys.length === 0 ? (
        <p className="muted">{t('passkey.none')}</p>
      ) : (
        <ul className="rows">
          {state.passkeys.map((passkey) => (
            <PasskeyRow
              key={passkey.id}
              passkey={passkey}
              busy={action.busy}
              canRename={state.enabled}
              onRename={rename}
              onRemove={remove}
            />
          ))}
        </ul>
      )}
      {state.enabled ? (
        full ? (
          <p className="muted">{t('passkey.limit')}</p>
        ) : (
          <form
            onSubmit={(event) => {
              event.preventDefault()
              add()
            }}
          >
            <Field
              label={t('passkey.nameOptional')}
              hint={t('passkey.nameHint')}
              value={name}
              maxLength={64}
              onChange={(event) => {
                setName(event.target.value)
              }}
            />
            <button type="submit" className="button" disabled={action.busy}>
              {t('passkey.add')}
            </button>
          </form>
        )
      ) : null}
    </Section>
  )
}
