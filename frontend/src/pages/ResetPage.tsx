import { useEffect, useState, type FormEvent } from 'react'
import { completeReset, requestPasswordReset, resetInfo, type ResetInfo } from '../api/auth'
import { errorMessage, providerName } from '../api/messages'
import { useSession } from '../auth/SessionContext'
import { useT } from '../i18n/I18n'
import { Field, Notice, useAction } from '../components/forms'
import { AuthCard as Card } from '../components/auth/AuthCard'
import { useCaptcha } from '../components/auth/Turnstile'
import { Link, useMailToken } from '../router/Router'

/** `/reset`: ask for a link. `/reset#token=...`: choose a new password. */
export function ResetPage() {
  const token = useMailToken()
  return token === null ? <RequestReset /> : <ChoosePassword token={token} />
}

function RequestReset() {
  const t = useT()
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const action = useAction()
  const captcha = useCaptcha('reset')

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!captcha.ready) return
    const captchaToken = captcha.token
    void action.run(async () => {
      try {
        await requestPasswordReset(email.trim(), captchaToken)
      } catch (error) {
        // The token was sent, so it is spent whatever the answer: the check runs again.
        if (captchaToken !== null) captcha.reset()
        throw error
      }
      setSent(true)
    })
  }

  if (sent) {
    return (
      <Card title={t('auth.checkInbox')}>
        <Notice kind="success">{t('reset.sent')}</Notice>
        <p className="card__links">
          <Link to="/signin">{t('auth.backToSignIn')}</Link>
        </p>
      </Card>
    )
  }

  return (
    <Card title={t('reset.title')}>
      <p>{t('reset.intro')}</p>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <form onSubmit={submit}>
        <Field
          label={t('common.email')}
          type="email"
          autoComplete="email"
          value={email}
          onChange={(e) => {
            setEmail(e.target.value)
          }}
          required
        />
        {captcha.element}
        <button
          className="button"
          type="submit"
          disabled={action.busy || !captcha.ready}
          aria-describedby={captcha.describedBy}
        >
          {t('reset.send')}
        </button>
      </form>
      <p className="card__links">
        <Link to="/signin">{t('auth.backToSignIn')}</Link>
      </p>
    </Card>
  )
}

type Methods =
  | { state: 'loading' }
  | { state: 'bad'; message: string }
  | { state: 'ok'; info: ResetInfo }

function ChoosePassword({ token }: { token: string }) {
  const t = useT()
  const [methods, setMethods] = useState<Methods>({ state: 'loading' })
  const [remove, setRemove] = useState<Set<string>>(new Set())
  const [removePasskeys, setRemovePasskeys] = useState<Set<string>>(new Set())
  const [password, setPassword] = useState('')
  const [done, setDone] = useState(false)
  const action = useAction()
  const { refresh } = useSession()

  useEffect(() => {
    let current = true
    resetInfo(token).then(
      (info) => {
        if (current) setMethods({ state: 'ok', info })
      },
      (error: unknown) => {
        if (current) setMethods({ state: 'bad', message: errorMessage(error) })
      },
    )
    return () => {
      current = false
    }
  }, [token])

  const toggle = (id: string, set: typeof setRemove = setRemove) => {
    set((previous) => {
      const next = new Set(previous)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    void action.run(async () => {
      const shown = methods.state === 'ok' ? methods.info : { methods: [], passkeys: [] }
      const keep = shown.methods.filter((method) => !remove.has(method.id)).map((method) => method.id)
      // Every passkey the page showed and the user left unticked; the server deletes the rest.
      const keepPasskeys = shown.passkeys.filter((p) => !removePasskeys.has(p.id)).map((p) => p.id)
      await completeReset(token, password, keep, keepPasskeys)
      // A reset revokes every session: update the nav without a reload.
      await refresh()
      setDone(true)
    })
  }

  if (done) {
    return (
      <Card title={t('reset.changedTitle')}>
        <Notice kind="success">{t('reset.changed')}</Notice>
        <p>
          <Link className="button" to="/signin">
            {t('auth.signIn')}
          </Link>
        </p>
      </Card>
    )
  }
  if (methods.state === 'loading') {
    return (
      <Card title={t('reset.chooseTitle')}>
        <p>{t('reset.checking')}</p>
      </Card>
    )
  }
  if (methods.state === 'bad') {
    return (
      <Card title={t('reset.chooseTitle')}>
        <Notice kind="error">{methods.message}</Notice>
        <p className="card__links">
          <Link to="/reset">{t('reset.newLink')}</Link>
        </p>
      </Card>
    )
  }

  return (
    <Card title={t('reset.chooseTitle')}>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <form onSubmit={submit}>
        <Field
          label={t('reset.newPassword')}
          type="password"
          autoComplete="new-password"
          hint={t('reset.passwordHint')}
          value={password}
          onChange={(e) => {
            setPassword(e.target.value)
          }}
          required
        />
        {methods.info.methods.length > 0 ? (
          <fieldset className="fieldset">
            <legend>{t('reset.linked')}</legend>
            <p className="field__hint">{t('reset.linkedHint')}</p>
            {methods.info.methods.map((method) => (
              <label key={method.id} className="check">
                <input
                  type="checkbox"
                  checked={remove.has(method.id)}
                  onChange={() => {
                    toggle(method.id)
                  }}
                />
                {t('reset.remove', { provider: providerName(method.provider) })}
              </label>
            ))}
          </fieldset>
        ) : null}
        {methods.info.passkeys.length > 0 ? (
          <fieldset className="fieldset">
            <legend>{t('reset.passkeys')}</legend>
            <p className="field__hint">{t('reset.passkeysHint')}</p>
            {methods.info.passkeys.map((passkey) => (
              <label key={passkey.id} className="check">
                <input
                  type="checkbox"
                  checked={removePasskeys.has(passkey.id)}
                  onChange={() => {
                    toggle(passkey.id, setRemovePasskeys)
                  }}
                />
                {t('reset.removePasskey', { name: passkey.label })}
              </label>
            ))}
          </fieldset>
        ) : null}
        <button className="button" type="submit" disabled={action.busy}>
          {t('reset.submit')}
        </button>
      </form>
    </Card>
  )
}
