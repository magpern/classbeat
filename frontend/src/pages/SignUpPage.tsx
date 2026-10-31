import { useState } from 'react'
import { signUp } from '../api/auth'
import { Notice } from '../components/forms'
import { AuthCard as Card } from '../components/auth/AuthCard'
import { AuthScreen } from '../components/auth/AuthScreen'
import { Link, safeReturnTo, useQuery } from '../router/Router'

export function SignUpPage() {
  const [sent, setSent] = useState(false)
  const query = useQuery()

  if (sent) {
    return (
      <Card title="Check your inbox">
        <Notice kind="success">
          If this address can be used, we sent a link to it. Open the link and type your password
          again to finish. The link works for 24 hours.
        </Notice>
        <p className="card__links">
          <Link to="/signin">Back to sign in</Link>
        </p>
      </Card>
    )
  }

  return (
    <AuthScreen
      mode="signup"
      returnTo={safeReturnTo(query.get('return_to'))}
      onSubmit={async ({ email, password, invite }) => {
        await signUp({ email, password, inviteCode: invite.trim() })
        setSent(true)
      }}
    />
  )
}
