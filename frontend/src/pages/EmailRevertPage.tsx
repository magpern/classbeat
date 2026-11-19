import { useState } from 'react'
import { revertEmailChange } from '../api/auth'
import { ApiError } from '../api/client'
import { errorMessage } from '../api/messages'
import { useSession } from '../auth/SessionContext'
import { useT } from '../i18n/I18n'
import { Notice, useAction } from '../components/forms'
import { AuthCard as Card } from '../components/auth/AuthCard'
import { Link, useMailToken } from '../router/Router'
import { Button } from '../components/ui/button'

/**
 * `/email-revert#token=...` from the notice to the old address (#1265). Public: no session is
 * needed and none is made. Opening the link changes nothing; only the button posts the token.
 * A success signs everyone out of the account (this browser too, if it was signed in to it), so
 * the session is refetched.
 */
export function EmailRevertPage() {
  const t = useT()
  const token = useMailToken()
  const { refresh } = useSession()
  const [done, setDone] = useState(false)
  const action = useAction()

  if (token === null) {
    return (
      <Card title={t('emailRevert.title')}>
        <Notice kind="error">{t('emailRevert.noLink')}</Notice>
      </Card>
    )
  }
  if (done) {
    return (
      <Card title={t('emailRevert.doneTitle')}>
        <Notice kind="success">{t('emailRevert.done')}</Notice>
        <p>
          <Button asChild>
            <Link to="/signin">{t('emailRevert.signIn')}</Link>
          </Button>
        </p>
      </Card>
    )
  }

  const revert = () => {
    void action.run(
      async () => {
        await revertEmailChange(token)
        setDone(true)
        await refresh()
      },
      (caught) =>
        caught instanceof ApiError && caught.reason === 'email_taken'
          ? t('emailRevert.taken')
          : errorMessage(caught),
    )
  }
  return (
    <Card title={t('emailRevert.title')}>
      <p>{t('emailRevert.intro')}</p>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <Button type="button" disabled={action.busy} onClick={revert}>
        {t('emailRevert.confirm')}
      </Button>
    </Card>
  )
}
