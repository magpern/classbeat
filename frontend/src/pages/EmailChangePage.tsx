import { useState } from 'react'
import { confirmEmailChange } from '../api/auth'
import { useSession } from '../auth/SessionContext'
import { useT } from '../i18n/I18n'
import { Notice, useAction } from '../components/forms'
import { AuthCard as Card } from '../components/auth/AuthCard'
import { Link, useMailToken } from '../router/Router'

/**
 * `/email-change#token=...` from the mail to the new address. Needs a signed-in session of the
 * account that asked; the link alone changes nothing and signs nobody in.
 */
export function EmailChangePage() {
  const t = useT()
  const token = useMailToken()
  const { status, session, refresh } = useSession()
  const [changed, setChanged] = useState<string | null>(null)
  const action = useAction()

  if (token === null) {
    return (
      <Card title={t('emailChange.title')}>
        <Notice kind="error">{t('emailChange.noLink')}</Notice>
      </Card>
    )
  }
  if (changed !== null) {
    return (
      <Card title={t('emailChange.changedTitle')}>
        <Notice kind="success">{t('emailChange.changed', { email: changed })}</Notice>
        <p>
          <Link className="button" to="/account">
            {t('emailChange.back')}
          </Link>
        </p>
      </Card>
    )
  }
  if (status === 'loading') {
    return (
      <Card title={t('emailChange.title')}>
        <p>{t('common.loading')}</p>
      </Card>
    )
  }
  if (!session?.authenticated) {
    return (
      <Card title={t('emailChange.title')}>
        <Notice kind="info">
          {t('emailChange.signInFirst')}
        </Notice>
        <p>
          <Link className="button" to="/signin">
            {t('auth.signIn')}
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
    <Card title={t('emailChange.title')}>
      <p>{t('emailChange.confirmIntro', { email: session.user?.email ?? '' })}</p>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <button className="button" type="button" disabled={action.busy} onClick={confirm}>
        {t('emailChange.confirm')}
      </button>
    </Card>
  )
}
