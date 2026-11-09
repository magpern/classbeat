import { useState, type FormEvent } from 'react'
import { deleteAccount } from '../../api/auth'
import { useGuard } from '../../auth/prompts'
import { useSession } from '../../auth/SessionContext'
import { useT } from '../../i18n/I18n'
import { useRouter } from '../../router/Router'
import { Field, Notice, useAction } from '../forms'
import { Button } from '../ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from '../ui/dialog'

/**
 * Delete my account (ADR 0014 section 3). The user confirms by typing their email; the request
 * (`POST /api/account/delete`) carries it, and the server checks it against the account's. It
 * goes through the guard, which asks for recent authentication and, with two-factor on, a step-up
 * code, exactly as the server answers. The dialog closes before the request so the guard's prompt
 * is on top. The typed text is sent only to that request and kept nowhere.
 */
export function DeleteAccount({ email }: { email: string }) {
  const t = useT()
  const guard = useGuard()
  const { refresh } = useSession()
  const { navigate } = useRouter()
  const action = useAction()
  const [open, setOpen] = useState(false)
  const [typed, setTyped] = useState('')
  const matches = email !== '' && typed.trim().toLowerCase() === email.trim().toLowerCase()

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!matches) return
    const confirmEmail = typed.trim()
    setOpen(false)
    setTyped('')
    void action
      .run(async () => {
        await guard(() => deleteAccount(confirmEmail))
        return true
      })
      .then(async (deleted) => {
        if (deleted === true) {
          await refresh()
          navigate('/')
        }
      })
  }

  return (
    <div className="flex flex-col gap-2">
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <Dialog
        open={open}
        onOpenChange={(next) => {
          setOpen(next)
          if (!next) setTyped('')
        }}
      >
        <div>
          {/* The trigger belongs to the dialog, so focus returns to it when the dialog closes. */}
          <DialogTrigger asChild>
            <Button variant="danger" disabled={action.busy}>
              {t('delete.button')}
            </Button>
          </DialogTrigger>
        </div>
        {open ? (
          <DialogContent aria-describedby="delete-account-description">
            <DialogTitle>{t('delete.title')}</DialogTitle>
            <DialogDescription id="delete-account-description">{t('delete.text')}</DialogDescription>
            <p className="m-0">{t('delete.also')}</p>
            <p className="m-0">{t('delete.stays')}</p>
            <form onSubmit={submit}>
              <Field
                label={t('delete.emailLabel')}
                type="email"
                autoComplete="off"
                spellCheck={false}
                value={typed}
                onChange={(e) => {
                  setTyped(e.target.value)
                }}
              />
              <div className="flex flex-wrap gap-2">
                <Button type="submit" variant="danger" disabled={!matches}>
                  {t('delete.confirm')}
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => {
                    setOpen(false)
                    setTyped('')
                  }}
                >
                  {t('delete.keep')}
                </Button>
              </div>
            </form>
          </DialogContent>
        ) : null}
      </Dialog>
    </div>
  )
}
