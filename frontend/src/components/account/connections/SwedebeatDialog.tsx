import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ApiError } from '../../../api/client'
import { connectSwedebeat, setSwedebeatPreference, type Connection } from '../../../api/connections'
import { errorMessage } from '../../../api/messages'
import { useGuard } from '../../../auth/prompts'
import type { CatalogueKey } from '../../../i18n/en'
import { useT } from '../../../i18n/I18n'
import { t as translate } from '../../../i18n/t'
import { Field, Notice, useAction } from '../../forms'
import { Button } from '../../ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '../../ui/dialog'

const CONNECT_ERRORS: Record<string, CatalogueKey> = {
  invalid_credentials: 'swed.err.invalid_credentials',
  upstream_unavailable: 'swed.err.upstream_unavailable',
  app_key_unavailable: 'swed.err.app_key_unavailable',
  feature_off: 'swed.err.feature_off',
  unknown: 'swed.err.unknown',
}

function connectError(error: unknown): string {
  if (error instanceof ApiError && error.status !== 429 && error.reason !== null) {
    const known = Object.hasOwn(CONNECT_ERRORS, error.reason) ? CONNECT_ERRORS[error.reason] : undefined
    if (known !== undefined) return translate(known)
  }
  return errorMessage(error)
}

/**
 * Connect Swedebeat (ADR 0028 section 1, item 2). The stored "I use Swedebeat" preference (#525) is
 * what lets the backend talk to Swedebeat at all, so the dialog stores it as on, then connects. If
 * the dialog ends without a connection the preference is put back as it was: a failed attempt
 * leaves nothing half-set. Another device being signed in is asked about, not decided.
 *
 * Every call the dialog makes goes through one queue, so a rollback never lands before an attempt it
 * follows (a close during a slow connect) or after one that came later (a reopen and a new try), and
 * whether anything was connected is read when the rollback runs, not when it was asked for.
 */
export function SwedebeatDialog({
  open,
  wasUsing,
  again,
  onClose,
  onConnected,
}: {
  open: boolean
  /** The stored preference before the dialog opened. */
  wasUsing: boolean
  again: boolean
  onClose: () => void
  onConnected: (connection: Connection) => void
}) {
  const t = useT()
  const guard = useGuard()
  const action = useAction()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [taken, setTaken] = useState(false)
  const done = useRef(false)
  const queue = useRef<Promise<void>>(Promise.resolve())

  const enqueue = <T,>(job: () => Promise<T>): Promise<T> => {
    const result = queue.current.then(job)
    queue.current = result.then(
      () => undefined,
      () => undefined,
    )
    return result
  }

  // Each opening starts with nothing connected, whatever an earlier opening ended with.
  useEffect(() => {
    if (open) done.current = false
  }, [open])

  const close = () => {
    action.clear()
    setTaken(false)
    // Put the preference back when nothing was connected (best effort: a failure leaves it on, which
    // only means Swedebeat is offered, never that anything is sent without a connection).
    if (!wasUsing) {
      void enqueue(async () => {
        if (!done.current) await setSwedebeatPreference(false)
      }).catch(() => undefined)
    }
    onClose()
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const sent = { email: email.trim(), password, force: taken }
    void action
      .run(
        () =>
          enqueue(async () => {
            await setSwedebeatPreference(true)
            const made = await guard(() => connectSwedebeat(sent.email, sent.password, sent.force))
            done.current = true
            setTaken(false)
            onConnected(made)
          }),
        (error) => {
          if (error instanceof ApiError && error.reason === 'device_in_use') {
            setTaken(true)
            return t('swed.deviceInUse')
          }
          setTaken(false)
          return connectError(error)
        },
      )
      .finally(() => {
        setPassword('') // the password is not kept on the page after the attempt
      })
    setEmail(sent.email)
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close()
      }}
    >
      <DialogContent>
        <DialogTitle>{t('swed.connect')}</DialogTitle>
        <DialogDescription>{t('conn.noPassword')}</DialogDescription>
        {action.error ? <Notice kind="error">{action.error}</Notice> : null}
        <form onSubmit={submit} className="flex flex-col gap-3">
          <Field
            label={t('swed.email')}
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value)
            }}
            required
          />
          <Field
            label={t('swed.password')}
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value)
            }}
            required
          />
          {taken ? <p className="m-0 text-[0.9375rem] text-muted">{t('swed.again')}</p> : null}
          <div className="flex flex-wrap gap-3">
            <Button type="submit" disabled={action.busy}>
              {taken ? t('swed.anyway') : again ? t('swed.connectAgain') : t('swed.connect')}
            </Button>
            {taken ? (
              <Button variant="secondary" onClick={close}>
                {t('swed.keepOther')}
              </Button>
            ) : null}
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
