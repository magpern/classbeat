import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { requestEmailChange } from '../../api/auth'
import { useGuard } from '../../auth/prompts'
import { Trans, useT } from '../../i18n/I18n'
import { Button } from '../ui/button'
import { Field, Notice, Section, useAction } from '../forms'

/**
 * The email address section: the current address and a "Change" button. The form for a new
 * address opens on demand; Cancel, Escape and a sent request fold it away again.
 */
export function ChangeEmail({ current }: { current: string }) {
  const t = useT()
  const guard = useGuard()
  const formId = useId()
  const [open, setOpen] = useState(false)
  const [email, setEmail] = useState('')
  const [sentTo, setSentTo] = useState<string | null>(null)
  const action = useAction()
  const input = useRef<HTMLInputElement>(null)
  const opener = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (open) input.current?.focus()
  }, [open])

  const close = () => {
    setOpen(false)
    setEmail('')
    action.clear()
    opener.current?.focus()
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    void action.run(async () => {
      const wanted = email.trim()
      await guard(() => requestEmailChange(wanted))
      setSentTo(wanted)
      close()
    })
  }

  const onKeyDown = (event: KeyboardEvent) => {
    // A request in flight finishes first: folding the form away would hide its outcome.
    if (event.key === 'Escape' && !action.busy) close()
  }

  return (
    <Section title={t('cred.email.title')}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <p className="wrap-anywhere">
          <Trans id="cred.email.current" slots={{ email: <strong>{current}</strong> }} />
        </p>
        <Button
          ref={opener}
          variant="secondary"
          aria-expanded={open}
          aria-controls={formId}
          onClick={() => {
            if (open) close()
            else {
              setSentTo(null)
              setOpen(true)
            }
          }}
        >
          {t('cred.email.edit')}
        </Button>
      </div>
      {sentTo ? <Notice kind="success">{t('cred.email.sent', { email: sentTo })}</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <div id={formId}>
        {open ? (
          <form onSubmit={submit} onKeyDown={onKeyDown}>
            <Field
              ref={input}
              label={t('cred.email.newLabel')}
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value)
              }}
              required
            />
            <div className="flex flex-wrap gap-3">
              <Button type="submit" disabled={action.busy}>
                {t('cred.email.change')}
              </Button>
              <Button variant="secondary" disabled={action.busy} onClick={close}>
                {t('common.cancel')}
              </Button>
            </div>
          </form>
        ) : null}
      </div>
    </Section>
  )
}
