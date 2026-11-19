import { useState, type FormEvent } from 'react'
import { changePassword } from '../../api/auth'
import { useGuard } from '../../auth/prompts'
import { useT } from '../../i18n/I18n'
import { Field, Notice, Section, useAction } from '../forms'

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
