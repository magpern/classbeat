import { useCallback, useEffect, useState } from 'react'
import { getIdentities, startOAuth, unlinkIdentity, type IdentitiesState } from '../../api/auth'
import { providerName } from '../../api/messages'
import { useGuard } from '../../auth/prompts'
import { goToProvider } from '../../lib/navigation'
import { Notice, Section, useAction } from '../forms'

/** Linked providers and the password: link, unlink (recent auth and step-up as needed). */
export function SignInMethods() {
  const guard = useGuard()
  const [state, setState] = useState<IdentitiesState | null>(null)
  const action = useAction()

  const load = useCallback(async () => {
    setState(await getIdentities())
  }, [])

  useEffect(() => {
    let current = true
    getIdentities().then(
      (loaded) => {
        if (current) setState(loaded)
      },
      () => undefined,
    )
    return () => {
      current = false
    }
  }, [])

  const unlink = (id: string) => {
    void action.run(async () => {
      await guard(() => unlinkIdentity(id))
      await load()
    })
  }
  const link = (provider: string) => {
    void action.run(async () => {
      const url = await guard(() => startOAuth(provider, 'link', { returnTo: '/account' }))
      goToProvider(url)
    })
  }

  if (state === null) {
    return (
      <Section title="Sign-in methods">
        <p>Loading</p>
      </Section>
    )
  }
  const linked = new Set(state.identities.map((item) => item.provider))
  const linkable = state.available.filter((provider) => !linked.has(provider))
  return (
    <Section title="Sign-in methods">
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <ul className="rows">
        <li>
          <span>Password</span>
          <span className="muted">{state.hasPassword ? 'Set' : 'Not set'}</span>
        </li>
        {state.identities.map((item) => (
          <li key={item.id}>
            <span>{providerName(item.provider)}</span>
            <button
              type="button"
              className="button button--secondary"
              disabled={action.busy}
              onClick={() => {
                unlink(item.id)
              }}
            >
              Unlink {providerName(item.provider)}
            </button>
          </li>
        ))}
      </ul>
      {linkable.map((provider) => (
        <button
          key={provider}
          type="button"
          className="button button--secondary"
          disabled={action.busy}
          onClick={() => {
            link(provider)
          }}
        >
          Link {providerName(provider)}
        </button>
      ))}
    </Section>
  )
}
