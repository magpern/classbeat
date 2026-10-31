import { useState, type FormEvent } from 'react'
import { deleteAccount } from '../../api/auth'
import { useGuard } from '../../auth/prompts'
import { useSession } from '../../auth/SessionContext'
import { useRouter } from '../../router/Router'
import { Field, Notice, useAction } from '../forms'
import { Button } from '../ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '../ui/dialog'

/**
 * Delete my account (ADR 0003 section 6). The user confirms by typing their email; the request
 * (`DELETE /api/auth/account`) then goes through the guard, which asks for recent authentication
 * and, with two-factor on, a step-up code, exactly as the server answers. The dialog closes before
 * the request so the guard's prompt is on top. The typed text is never sent or kept.
 */
export function DeleteAccount({ email }: { email: string }) {
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
    setOpen(false)
    setTyped('')
    void action
      .run(async () => {
        await guard(deleteAccount)
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
          <Button
            variant="danger"
            disabled={action.busy}
            onClick={() => {
              setOpen(true)
            }}
          >
            Delete my account
          </Button>
        </div>
        {open ? (
          <DialogContent aria-describedby="delete-account-description">
            <DialogTitle>Delete your account?</DialogTitle>
            <DialogDescription id="delete-account-description">
              This deletes your account, your classes, your plan and your connections. It cannot be
              undone. Workouts saved to MOWL and playlists in your Spotify stay there. Type your email
              to confirm.
            </DialogDescription>
            <form onSubmit={submit}>
              <Field
                label="Your email"
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
                  Delete for good
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => {
                    setOpen(false)
                    setTyped('')
                  }}
                >
                  Keep my account
                </Button>
              </div>
            </form>
          </DialogContent>
        ) : null}
      </Dialog>
    </div>
  )
}
