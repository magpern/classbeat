import { useEffect, useState, type ReactNode } from 'react'
import { listPasskeys } from '../../api/passkeys'
import { passkeyErrorMessage, reauthWithPasskey } from '../../auth/passkeyCeremony'
import { useSession } from '../../auth/SessionContext'
import { passkeysSupported } from '../../auth/webauthn'
import { useT } from '../../i18n/I18n'
import type { useAction } from '../forms'

interface Props {
  action: ReturnType<typeof useAction>
  onDone: (success: boolean) => void
}

/**
 * "Confirm with a passkey" in the re-authentication prompt (ADR 0019 section 8). Offered only
 * where the flag is on, the browser has WebAuthn and the user has a passkey to use: a passkey
 * confirms identity but is never a step-up, so the step-up prompt has no such button.
 */
export function PasskeyReauth({ action, onDone }: Props): ReactNode {
  const t = useT()
  const { session } = useSession()
  const [has, setHas] = useState(false)
  const offered = session?.passkeys === true && passkeysSupported()

  useEffect(() => {
    if (!offered) return
    const controller = new AbortController()
    listPasskeys(controller.signal).then(
      (loaded) => {
        setHas(loaded.passkeys.length > 0)
      },
      () => undefined,
    )
    return () => {
      controller.abort()
    }
  }, [offered])

  if (!offered || !has) return null
  return (
    <button
      className="button button--secondary"
      type="button"
      disabled={action.busy}
      onClick={() => {
        void action.run(async () => {
          await reauthWithPasskey()
          onDone(true)
        }, passkeyErrorMessage)
      }}
    >
      {t('guard.confirmPasskey')}
    </button>
  )
}
