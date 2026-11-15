import { useState } from 'react'
import { ApiError } from '../api/client'
import { REQUEST_EXPIRED, answerPlayerConsent } from '../api/playerConsent'
import { useGuard } from '../auth/prompts'
import { useSession } from '../auth/SessionContext'
import { AuthCard as Card } from '../components/auth/AuthCard'
import { Notice, useAction } from '../components/forms'
import { useT } from '../i18n/I18n'
import { goToProvider } from '../lib/navigation'
import { Link, useQuery } from '../router/Router'

// `/player-consent?r=...`: the provider's consent page (ADR 0034 section 1). The server sends a
// signed-in browser here the first time classbeat-player asks to sign it in, and when a
// `prompt=login` request still needs a re-authentication or step-up: the guard asks for it and
// the answer is sent again. Allow or Cancel both end in a full load of the player's address.

const REQUEST_ID = /^[0-9a-f-]{36}$/

function Consent({ requestId }: { requestId: string }) {
  const t = useT()
  const guard = useGuard()
  const { session } = useSession()
  const action = useAction()
  const [leaving, setLeaving] = useState(false)
  const [expired, setExpired] = useState(false)

  const answer = (allow: boolean) => {
    void action.run(async () => {
      try {
        const target = await guard(() => answerPlayerConsent(requestId, allow))
        goToProvider(target) // http(s) only: throws before leaving for any other scheme
        setLeaving(true)
      } catch (caught) {
        if (caught instanceof ApiError && caught.reason === REQUEST_EXPIRED) setExpired(true)
        else throw caught
      }
    })
  }

  if (expired) return <Notice kind="error">{t('playerConsent.expired')}</Notice>
  if (leaving) return <p>{t('playerConsent.leaving')}</p>
  return (
    <>
      <p>{t('playerConsent.intro', { email: session?.user?.email ?? '' })}</p>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <p className="flex flex-wrap gap-3">
        <button className="button" type="button" disabled={action.busy} onClick={() => { answer(true) }}>
          {t('playerConsent.allow')}
        </button>
        <button className="button button--secondary" type="button" disabled={action.busy} onClick={() => { answer(false) }}>
          {t('playerConsent.deny')}
        </button>
      </p>
    </>
  )
}

export function PlayerConsentPage() {
  const t = useT()
  const { status, session } = useSession()
  const requestId = useQuery().get('r')
  let body
  if (requestId === null || !REQUEST_ID.test(requestId)) {
    body = <Notice kind="error">{t('playerConsent.expired')}</Notice>
  } else if (status === 'loading') {
    body = <p>{t('common.loading')}</p>
  } else if (!session?.authenticated) {
    // Back through the server's continue step, which checks the request again (and this browser).
    const returnTo = encodeURIComponent(`/oidc/authorize/continue?r=${requestId}`)
    body = (
      <p>
        <Link className="button" to={`/signin?return_to=${returnTo}`}>
          {t('auth.signIn')}
        </Link>
      </p>
    )
  } else {
    body = <Consent requestId={requestId} />
  }
  return <Card title={t('playerConsent.title')}>{body}</Card>
}
