import { useEffect, useState } from 'react'
import { signUp } from '../api/auth'
import { getSignupMode } from '../api/inviteRequest'
import { oauthOutcomeMessage } from '../api/messages'
import { Notice } from '../components/forms'
import { useT } from '../i18n/I18n'
import { AuthCard as Card } from '../components/auth/AuthCard'
import { AuthScreen } from '../components/auth/AuthScreen'
import { InviteRequestForm } from '../components/auth/InviteRequestForm'
import { Link, safeReturnTo, useQuery } from '../router/Router'

export function SignUpPage() {
  const t = useT()
  const [sent, setSent] = useState(false)
  const query = useQuery()
  // A provider sign-up that failed comes back here (#617), e.g. an invite used up meanwhile.
  const outcome = query.get('oauth')
  // ADR 0033: "Request an invite" exists only while sign-up is invite-only. Undefined until read;
  // null when it could not be read, which shows no request link.
  const [mode, setMode] = useState<Awaited<ReturnType<typeof getSignupMode>> | undefined>(undefined)
  useEffect(() => {
    const controller = new AbortController()
    getSignupMode(controller.signal).then(setMode, () => {
      if (!controller.signal.aborted) setMode(null)
    })
    return () => {
      controller.abort()
    }
  }, [])
  const invitesOnly = mode === 'invite'
  const wantsRequest = query.get('request') === 'invite'

  if (sent) {
    return (
      <Card title={t('auth.checkInbox')}>
        <Notice kind="success">{t('signup.sent')}</Notice>
        <p className="card__links">
          <Link to="/signin">{t('auth.backToSignIn')}</Link>
        </p>
      </Card>
    )
  }

  if (wantsRequest && mode === undefined) return null
  if (wantsRequest && invitesOnly) return <InviteRequestForm />

  return (
    <AuthScreen
      mode="signup"
      inviteRequestTo={invitesOnly ? '/signup?request=invite' : null}
      returnTo={safeReturnTo(query.get('return_to'))}
      notice={outcome ? oauthOutcomeMessage(outcome) : null}
      onSubmit={async ({ email, password, invite, captchaToken }) => {
        await signUp({ email, password, inviteCode: invite.trim(), captchaToken })
        setSent(true)
      }}
    />
  )
}
