import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ApiError } from '../api/client'
import { ReauthPrompt, StepUpPrompt } from './GuardPrompts'
import { PromptCancelled, GuardContext, type Guard } from './prompts'
import { useSession } from './SessionContext'

type Kind = 'reauth' | 'stepup'

interface Waiter {
  resolve: () => void
  reject: (reason: unknown) => void
}

interface Pending {
  kind: Kind
  /** Every guarded action waiting on this prompt (the first and any that came in meanwhile). */
  waiters: Waiter[]
  /** Resolves once the prompt is closed, however it ended. */
  settled: Promise<void>
  settle: () => void
}

const MAX_PROMPTS = 3

export function GuardProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null)
  const { refresh } = useSession()

  const open = useRef<Pending | null>(null)
  const unmounted = useRef(false)

  // One prompt at a time. A second request for the same kind joins the open prompt; one for the
  // other kind waits until it is closed. No promise is left unresolved.
  const ask = useCallback(function askFor(kind: Kind): Promise<void> {
    if (unmounted.current) return Promise.reject(new PromptCancelled())
    const current = open.current
    if (current !== null && current.kind === kind) {
      return new Promise<void>((resolve, reject) => {
        current.waiters.push({ resolve, reject })
      })
    }
    if (current !== null) return current.settled.then(() => askFor(kind))
    return new Promise<void>((resolve, reject) => {
      let settle!: () => void
      const settled = new Promise<void>((done) => {
        settle = done
      })
      const next: Pending = {
        kind,
        waiters: [{ resolve, reject }],
        settled,
        settle,
      }
      open.current = next
      setPending(next)
    })
  }, [])

  useEffect(() => {
    unmounted.current = false
    return () => {
      unmounted.current = true
      const current = open.current
      open.current = null
      current?.waiters.forEach((waiter) => {
        waiter.reject(new PromptCancelled())
      })
      current?.settle()
    }
  }, [])

  const guard = useCallback<Guard>(
    async (action) => {
      for (let prompts = 0; prompts < MAX_PROMPTS; prompts++) {
        try {
          return await action()
        } catch (error) {
          if (!(error instanceof ApiError) || error.status !== 403) throw error
          if (error.reason === 'reauth_required') await ask('reauth')
          else if (error.reason === 'stepup_required') await ask('stepup')
          else throw error
        }
      }
      return action()
    },
    [ask],
  )

  const finish = useCallback(
    async (done: boolean) => {
      const current = open.current
      if (current === null) return
      open.current = null
      setPending(null)
      if (done) {
        await refresh()
        current.waiters.forEach((waiter) => {
          waiter.resolve()
        })
      } else {
        current.waiters.forEach((waiter) => {
          waiter.reject(new PromptCancelled())
        })
      }
      current.settle()
    },
    [refresh],
  )

  const value = useMemo(() => guard, [guard])
  return (
    <GuardContext.Provider value={value}>
      {children}
      {pending ? (
        <div className="overlay">
          <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="prompt-title">
            {pending.kind === 'reauth' ? (
              <ReauthPrompt onDone={(ok) => void finish(ok)} />
            ) : (
              <StepUpPrompt onDone={(ok) => void finish(ok)} />
            )}
          </div>
        </div>
      ) : null}
    </GuardContext.Provider>
  )
}
