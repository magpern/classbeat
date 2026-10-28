import { useCallback, useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import {
  getIdentities,
  reauthWithPassword,
  reauthWithTotp,
  startOAuth,
  stepUp,
  type IdentitiesState,
} from '../api/auth'
import { ApiError } from '../api/client'
import { providerName } from '../api/messages'
import { Field, Notice, useAction } from '../components/forms'
import { goToProvider } from '../lib/navigation'
import { PromptCancelled, GuardContext, type Guard } from './prompts'
import { useSession } from './SessionContext'

type Kind = 'reauth' | 'stepup'

interface Pending {
  kind: Kind
  resolve: () => void
  reject: (reason: unknown) => void
}

const MAX_PROMPTS = 3

export function GuardProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null)
  const { refresh } = useSession()

  const ask = useCallback(
    (kind: Kind) =>
      new Promise<void>((resolve, reject) => {
        setPending({ kind, resolve, reject })
      }),
    [],
  )

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
      const current = pending
      setPending(null)
      if (current === null) return
      if (done) {
        await refresh()
        current.resolve()
      } else {
        current.reject(new PromptCancelled())
      }
    },
    [pending, refresh],
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

interface PromptProps {
  onDone: (success: boolean) => void
}

function ReauthPrompt({ onDone }: PromptProps) {
  const { session } = useSession()
  const [state, setState] = useState<IdentitiesState | null>(null)
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const action = useAction()
  const isAdmin = session?.user?.role === 'admin'

  useEffect(() => {
    const controller = new AbortController()
    getIdentities(controller.signal).then(setState, () => undefined)
    return () => {
      controller.abort()
    }
  }, [])

  const submitPassword = (event: FormEvent) => {
    event.preventDefault()
    void action.run(async () => {
      await reauthWithPassword(password)
      onDone(true)
    })
  }
  const submitCode = (event: FormEvent) => {
    event.preventDefault()
    void action.run(async () => {
      await reauthWithTotp(code)
      onDone(true)
    })
  }
  const viaProvider = (provider: string) => {
    void action.run(async () => {
      const url = await startOAuth(provider, 'reauth', {
        returnTo: window.location.pathname + window.location.search,
      })
      goToProvider(url)
    })
  }

  const linked = state?.identities.map((item) => item.provider) ?? []
  return (
    <>
      <h2 id="prompt-title">Confirm it is you</h2>
      <p>This change needs a recent sign-in. Confirm your identity to continue.</p>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {state?.hasPassword ? (
        <form onSubmit={submitPassword}>
          <Field
            label="Password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value)
            }}
            required
          />
          <button className="button" type="submit" disabled={action.busy}>
            Confirm with password
          </button>
        </form>
      ) : null}
      {isAdmin ? (
        <form onSubmit={submitCode}>
          <Field
            label="Authenticator or recovery code"
            autoComplete="one-time-code"
            value={code}
            onChange={(e) => {
              setCode(e.target.value)
            }}
            required
          />
          <button className="button" type="submit" disabled={action.busy}>
            Confirm with code
          </button>
        </form>
      ) : null}
      {linked.map((provider) => (
        <button
          key={provider}
          className="button button--secondary"
          type="button"
          disabled={action.busy}
          onClick={() => {
            viaProvider(provider)
          }}
        >
          Confirm with {providerName(provider)}
        </button>
      ))}
      <button
        className="button button--link"
        type="button"
        onClick={() => {
          onDone(false)
        }}
      >
        Cancel
      </button>
    </>
  )
}

function StepUpPrompt({ onDone }: PromptProps) {
  const [code, setCode] = useState('')
  const action = useAction()
  const submit = (event: FormEvent) => {
    event.preventDefault()
    void action.run(async () => {
      await stepUp(code)
      onDone(true)
    })
  }
  return (
    <>
      <h2 id="prompt-title">Enter a two-factor code</h2>
      <p>This change needs a code from your authenticator app, or one of your recovery codes.</p>
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <form onSubmit={submit}>
        <Field
          label="Authenticator or recovery code"
          autoComplete="one-time-code"
          inputMode="text"
          value={code}
          onChange={(e) => {
            setCode(e.target.value)
          }}
          required
        />
        <button className="button" type="submit" disabled={action.busy}>
          Continue
        </button>
      </form>
      <button
        className="button button--link"
        type="button"
        onClick={() => {
          onDone(false)
        }}
      >
        Cancel
      </button>
    </>
  )
}
