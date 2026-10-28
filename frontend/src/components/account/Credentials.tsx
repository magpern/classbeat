import { useState, type FormEvent } from 'react'
import { changePassword, requestEmailChange } from '../../api/auth'
import { useGuard } from '../../auth/prompts'
import { Field, Notice, Section, useAction } from '../forms'

export function ChangeEmail({ current }: { current: string }) {
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
    <Section title="Email address">
      <p>
        Your address is <strong>{current}</strong>.
      </p>
      {sentTo ? (
        <Notice kind="success">
          If {sentTo} can be used, a confirmation link is on its way there. Open it while signed
          in to finish. Your address changes only then.
        </Notice>
      ) : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <form onSubmit={submit}>
        <Field
          label="New email address"
          type="email"
          autoComplete="email"
          value={email}
          onChange={(e) => {
            setEmail(e.target.value)
          }}
          required
        />
        <button className="button" type="submit" disabled={action.busy}>
          Change email
        </button>
      </form>
    </Section>
  )
}

export function ChangePassword() {
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
    <Section title="Password">
      {saved ? (
        <Notice kind="success">Password saved. Your other devices were signed out.</Notice>
      ) : null}
      {mismatch ? <Notice kind="error">The two passwords differ.</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <form onSubmit={submit}>
        <Field
          label="New password"
          type="password"
          autoComplete="new-password"
          hint="12 to 128 characters."
          value={password}
          onChange={(e) => {
            setPassword(e.target.value)
          }}
          required
        />
        <Field
          label="Repeat the new password"
          type="password"
          autoComplete="new-password"
          value={repeat}
          onChange={(e) => {
            setRepeat(e.target.value)
          }}
          required
        />
        <button className="button" type="submit" disabled={action.busy}>
          Save password
        </button>
      </form>
    </Section>
  )
}
