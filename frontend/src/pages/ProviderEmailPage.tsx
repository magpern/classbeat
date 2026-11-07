import { useEffect, useState, type FormEvent } from 'react'
import { getPendingSignup, submitPendingEmail, type PendingSignup } from '../api/auth'
import { providerName } from '../api/messages'
import { useT } from '../i18n/I18n'
import { Field, Notice, useAction } from '../components/forms'
import { AuthCard as Card } from '../components/auth/AuthCard'
import { useCaptcha } from '../components/auth/Turnstile'
import { Link } from '../router/Router'

/**
 * The hand-off for a provider sign-in whose email is missing or unverified (ADR 0003 section
 * 5). Only an address is sent; the provider identity stays on the server, tied to this browser.
 */
export function ProviderEmailPage() {
  const t = useT()
  const [pending, setPending] = useState<PendingSignup | null>(null)
  const [email, setEmail] = useState('')
  const [sentTo, setSentTo] = useState<string | null>(null)
  const action = useAction()
  const captcha = useCaptcha('signup_email')

  useEffect(() => {
    const controller = new AbortController()
    getPendingSignup(controller.signal).then(setPending, () => {
      setPending({ pending: false, provider: null, awaitingEmail: false })
    })
    return () => {
      controller.abort()
    }
  }, [])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!captcha.ready) return
    const captchaToken = captcha.token
    void action.run(async () => {
      try {
        await submitPendingEmail(email.trim(), captchaToken)
      } catch (error) {
        // The token was sent, so it is spent whatever the answer: the check runs again.
        if (captchaToken !== null) captcha.reset()
        throw error
      }
      setSentTo(email.trim())
    })
  }

  if (pending === null) {
    return (
      <Card title={t('providerEmail.loadingTitle')}>
        <p>{t('common.loading')}</p>
      </Card>
    )
  }

  if (sentTo !== null) {
    return (
      <Card title={t('auth.checkInbox')}>
        <Notice kind="success">{t('providerEmail.sent', { email: sentTo })}</Notice>
        <p>{t('providerEmail.mistyped')}</p>
        <p className="card__links">
          <Link to="/signin">{t('providerEmail.again')}</Link>
        </p>
      </Card>
    )
  }

  if (!pending.pending || !pending.awaitingEmail) {
    return (
      <Card title={t('providerEmail.nothingTitle')}>
        <Notice kind="info">{t('providerEmail.nothing')}</Notice>
        <p className="card__links">
          <Link to="/signin">{t('auth.backToSignIn')}</Link>
        </p>
      </Card>
    )
  }

  const name = pending.provider === null ? t('providerEmail.yourProvider') : providerName(pending.provider)
  return (
    <Card title={t('providerEmail.title')}>
      <p>{t('providerEmail.intro', { provider: name })}</p>
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
          {t('providerEmail.submit')}
        </button>
      </form>
    </Card>
  )
}
