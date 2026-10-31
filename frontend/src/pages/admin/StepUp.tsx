import { createContext, useCallback, useContext, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { stepUp } from '../../api/auth'
import { adminError } from '../../api/admin'
import { ApiError } from '../../api/client'
import { PromptCancelled } from '../../auth/prompts'
import { useSession } from '../../auth/SessionContext'
import { Field, Notice } from '../../components/forms'
import { Button } from '../../components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '../../components/ui/dialog'

/**
 * Runs an admin write. On 403 `stepup_required` it asks for a TOTP or recovery code
 * (`POST /api/auth/mfa/stepup`) and repeats the write **once**; a second refusal is the caller's
 * error. On 403 `mfa_required` (the 12-hour admin window ran out) the admin area goes back to its
 * two-factor check. The code lives only in the dialog's input while it is open: it is never kept
 * after the request, put in a URL or written to storage.
 */
export type AdminWrite = <T>(write: () => Promise<T>) => Promise<T>

const StepUpContext = createContext<AdminWrite | null>(null)
const MfaContext = createContext<(() => void) | null>(null)

export function useAdminWrite(): AdminWrite {
  const run = useContext(StepUpContext)
  if (run === null) throw new Error('useAdminWrite needs a StepUpProvider')
  return run
}

/** For reads: call it when a read answers 403 `mfa_required`, to show the two-factor check. */
export function useMfaRequired(): () => void {
  const onMfaRequired = useContext(MfaContext)
  if (onMfaRequired === null) throw new Error('useMfaRequired needs a StepUpProvider')
  return onMfaRequired
}

export function isReason(error: unknown, status: number, reason: string): boolean {
  return error instanceof ApiError && error.status === status && error.reason === reason
}

interface Waiter {
  resolve: () => void
  reject: (reason: unknown) => void
}

export function StepUpProvider({
  onMfaRequired,
  children,
}: {
  /** The admin's two-factor window is gone (or TOTP is not set up): show the check again. */
  onMfaRequired: () => void
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const waiters = useRef<Waiter[]>([])

  const ask = useCallback(
    () =>
      new Promise<void>((resolve, reject) => {
        waiters.current.push({ resolve, reject })
        setOpen(true)
      }),
    [],
  )

  const finish = useCallback((outcome: 'ok' | 'cancelled' | 'mfa') => {
    const waiting = waiters.current
    waiters.current = []
    setOpen(false)
    for (const waiter of waiting) {
      if (outcome === 'ok') waiter.resolve()
      else waiter.reject(new PromptCancelled())
    }
    if (outcome === 'mfa') onMfaRequired()
  }, [onMfaRequired])

  const run = useCallback<AdminWrite>(
    async (write) => {
      try {
        return await write()
      } catch (error) {
        if (isReason(error, 403, 'mfa_required')) {
          onMfaRequired()
          throw error
        }
        if (!isReason(error, 403, 'stepup_required')) throw error
      }
      await ask()
      try {
        return await write() // once: a second stepup_required is reported, not prompted again
      } catch (error) {
        if (isReason(error, 403, 'mfa_required')) onMfaRequired()
        throw error
      }
    },
    [ask, onMfaRequired],
  )

  const value = useMemo(() => run, [run])
  return (
    <StepUpContext.Provider value={value}>
      <MfaContext.Provider value={onMfaRequired}>{children}</MfaContext.Provider>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!next) finish('cancelled')
        }}
      >
        {open ? <StepUpDialog onDone={finish} /> : null}
      </Dialog>
    </StepUpContext.Provider>
  )
}

/** The prompt itself; unmounted (and its input with it) as soon as it closes. */
function StepUpDialog({ onDone }: { onDone: (outcome: 'ok' | 'cancelled' | 'mfa') => void }) {
  const { refresh } = useSession()
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const typed = code.trim()
    setCode('') // not kept once sent, right or wrong
    if (typed === '') return
    setBusy(true)
    setError(null)
    stepUp(typed).then(
      () => {
        onDone('ok')
      },
      (caught: unknown) => {
        setBusy(false)
        if (caught instanceof ApiError && caught.status === 401) {
          onDone('cancelled')
          void refresh() // the session is gone: the page asks the admin to sign in again
        } else if (isReason(caught, 409, 'totp_not_enabled') || isReason(caught, 403, 'mfa_required')) {
          onDone('mfa')
        } else {
          setError(adminError(caught))
        }
      },
    )
  }

  return (
    <DialogContent aria-describedby="stepup-description">
      <DialogTitle>Enter a two-factor code</DialogTitle>
      <DialogDescription id="stepup-description">
        Admin changes need a fresh code from your authenticator app, or one of your recovery codes.
      </DialogDescription>
      {error !== null ? <Notice kind="error">{error}</Notice> : null}
      <form onSubmit={submit}>
        <Field
          label="Authenticator or recovery code"
          autoComplete="one-time-code"
          spellCheck={false}
          value={code}
          onChange={(e) => {
            setCode(e.target.value)
          }}
          required
        />
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={busy}>
            Continue
          </Button>
          <Button
            variant="ghost"
            onClick={() => {
              onDone('cancelled')
            }}
          >
            Cancel
          </Button>
        </div>
      </form>
    </DialogContent>
  )
}
