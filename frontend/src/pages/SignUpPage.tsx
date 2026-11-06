import { useState } from 'react'
import { signUp } from '../api/auth'
import { oauthOutcomeMessage } from '../api/messages'
import { Notice } from '../components/forms'
import { useT } from '../i18n/I18n'
import { AuthCard as Card } from '../components/auth/AuthCard'
import { AuthScreen } from '../components/auth/AuthScreen'
import { Link, safeReturnTo, useQuery } from '../router/Router'

export function SignUpPage() {
  const t = useT()
  const [sent, setSent] = useState(false)
  const query = useQuery()
  // A provider sign-up that failed comes back here (#617), e.g. an invite used up meanwhile.
  const outcome = query.get('oauth')

  if (sent) {
    return (
      <Card title={t('auth.checkInbox')}>
        <Notice kind="success">
          {t('signup.sent')}
        </Notice>
        <p className="card__links">
          <Link to="/signin">{t('auth.backToSignIn')}</Link>
        </p>
      </Card>
    )
  }

  return (
    <AuthScreen
      mode="signup"
      returnTo={safeReturnTo(query.get('return_to'))}
      notice={outcome ? oauthOutcomeMessage(outcome) : null}
      onSubmit={async ({ email, password, invite }) => {
        await signUp({ email, password, inviteCode: invite.trim() })
        setSent(true)
      }}
    />
  )
}
