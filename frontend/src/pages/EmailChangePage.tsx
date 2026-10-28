import { useState } from 'react'
import { confirmEmailChange } from '../api/auth'
import { useSession } from '../auth/SessionContext'
import { Card, Notice, useAction } from '../components/forms'
import { Link, useMailToken } from '../router/Router'

/**
 * `/email-change#token=...` from the mail to the new address. Needs a signed-in session of the
 * account that asked; the link alone changes nothing and signs nobody in.
 */
export function EmailChangePage() {
  const token = useMailToken()
  const { status, session, refresh } = useSession()
  const [changed, setChanged] = useState<string | null>(null)
  const action = useAction()

  if (token === null) {
    return (
      <Card title="Confirm your new email">
        <Notice kind="error">This page needs the link from the confirmation mail.</Notice>
      </Card>
    )
  }
  if (changed !== null) {
    return (
      <Card title="Email changed">
        <Notice kind="success">Your account now uses {changed}. Other devices were signed out.</Notice>
        <p>
          <Link className="button" to="/account">
            Back to your account
          </Link>
        </p>
      </Card>
    )
  }
  if (status === 'loading') {
    return (
      <Card title="Confirm your new email">
        <p>Loading</p>
      </Card>
    )
  }
  if (!session?.authenticated) {
    return (
      <Card title="Confirm your new email">
        <Notice kind="info">
          Sign in to the account that asked for the change, then open the link in the mail again.
        </Notice>
        <p>
          <Link className="button" to="/signin">
            Sign in
          </Link>
        </p>
      </Card>
    )
  }

  const confirm = () => {
    void action.run(async () => {
      setChanged(await confirmEmailChange(token))
      await refresh()
    })
  }
  return (
    <Card title="Confirm your new email">
      <p>You are signed in as {session.user?.email}. Confirm that this account should use the new address.</p>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <button className="button" type="button" disabled={action.busy} onClick={confirm}>
        Confirm new email
      </button>
    </Card>
  )
}
