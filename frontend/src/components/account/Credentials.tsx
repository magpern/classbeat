import { useState, type FormEvent } from 'react'
import { changePassword, requestEmailChange } from '../../api/auth'
import { useGuard } from '../../auth/prompts'
import { Trans, useT } from '../../i18n/I18n'
import { Field, Notice, Section, useAction } from '../forms'

export function ChangeEmail({ current }: { current: string }) {
  const t = useT()
  const guard = useGuard()
  const [email, setEmail] = useState('')
  const [sentTo, setSentTo] = useState<string | null>(null)
  const action = useAction()

  const submit = (event: FormEvent) => {
    event.preventDefault()
    void action.run(async () => {
      const wanted = email.trim()
      await guard(() => requestEmailChange(wanted))
      setSentTo(wanted)
      setEmail('')
    })
  }

  return (
    <Section title={t('cred.email.title')}>
      <p>
        <Trans id="cred.email.current" slots={{ email: <strong>{current}</strong> }} />
      </p>
      {sentTo ? (
        <Notice kind="success">
          {t('cred.email.sent', { email: sentTo })}
        </Notice>
      ) : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <form onSubmit={submit}>
        <Field
          label={t('cred.email.newLabel')}
          type="email"
          autoComplete="email"
          value={email}
          onChange={(e) => {
            setEmail(e.target.value)
          }}
          required
        />
        <button className="button" type="submit" disabled={action.busy}>
          {t('cred.email.change')}
        </button>
      </form>
    </Section>
  )
}

export function ChangePassword() {
  const t = useT()
  const guard = useGuard()
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [saved, setSaved] = useState(false)
  const [mismatch, setMismatch] = useState(false)
  const action = useAction()

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setSaved(false)
    if (password !== repeat) {
      setMismatch(true)
      return
    }
    setMismatch(false)
    void action.run(async () => {
      await guard(() => changePassword(password))
      setSaved(true)
      setPassword('')
      setRepeat('')
    })
  }

  return (
    <Section title={t('cred.password.title')}>
      {saved ? <Notice kind="success">{t('cred.password.saved')}</Notice> : null}
      {mismatch ? <Notice kind="error">{t('cred.password.mismatch')}</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <form onSubmit={submit}>
        <Field
          label={t('cred.password.newLabel')}
          type="password"
          autoComplete="new-password"
          hint={t('cred.password.hint')}
          value={password}
          onChange={(e) => {
            setPassword(e.target.value)
          }}
          required
        />
        <Field
          label={t('cred.password.repeatLabel')}
          type="password"
          autoComplete="new-password"
          value={repeat}
          onChange={(e) => {
            setRepeat(e.target.value)
          }}
          required
        />
        <button className="button" type="submit" disabled={action.busy}>
          {t('cred.password.save')}
        </button>
      </form>
    </Section>
  )
}
