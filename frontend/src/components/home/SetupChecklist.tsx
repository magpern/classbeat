import { Check, Circle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getConnections } from '../../api/connections'
import { getSpotifyStatus } from '../../api/spotify'
import { Link } from '../../router/Router'
import { Card, CardTitle } from '../ui/card'

interface Step {
  key: string
  label: string
  done: boolean
  to?: string
  action?: string
}

type State = { status: 'loading' } | { status: 'ready'; steps: Step[] } | { status: 'hidden' }

/**
 * "Get set up": account, MOWL and, where song picking is on, Spotify. Built from the existing
 * connection APIs and shown until every step is done. A failed read hides it rather than
 * guessing.
 */
export function SetupChecklist() {
  const [state, setState] = useState<State>({ status: 'loading' })
  useEffect(() => {
    const abort = new AbortController()
    void (async () => {
      try {
        const connections = await getConnections()
        const spotify = await getSpotifyStatus(abort.signal)
        if (abort.signal.aborted) return
        const mowl = connections.find((c) => c.provider === 'mowl')
        const steps: Step[] = [
          { key: 'account', label: 'Create your account', done: true },
          {
            key: 'mowl',
            label: mowl?.status === 'needs_reconnect' ? 'Connect MOWL again' : 'Connect MOWL',
            done: mowl?.status === 'connected',
            to: '/account',
            action: 'Connect',
          },
        ]
        if (spotify !== null) {
          steps.push({
            key: 'spotify',
            label: spotify.status === 'needs_reconnect' ? 'Connect Spotify again (optional)' : 'Connect Spotify (optional)',
            done: spotify.status === 'connected',
            to: '/account/spotify',
            action: 'Connect',
          })
        }
        setState(steps.every((s) => s.done) ? { status: 'hidden' } : { status: 'ready', steps })
      } catch {
        if (!abort.signal.aborted) setState({ status: 'hidden' })
      }
    })()
    return () => {
      abort.abort()
    }
  }, [])

  if (state.status !== 'ready') return null
  return (
    <Card aria-labelledby="setup-title" role="region">
      <CardTitle id="setup-title">Get set up</CardTitle>
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {state.steps.map((step) => (
          <li key={step.key} className="flex min-h-11 items-center gap-3">
            {step.done ? (
              <Check aria-hidden="true" size={20} strokeWidth={2} className="text-ok" />
            ) : (
              <Circle aria-hidden="true" size={20} strokeWidth={2} className="text-muted" />
            )}
            <span className={step.done ? 'text-muted' : 'text-text'}>
              {step.label}
              <span className="sr-only">{step.done ? ', done' : ', not done yet'}</span>
            </span>
            {!step.done && step.to !== undefined ? (
              <Link
                to={step.to}
                className="ml-auto inline-flex min-h-11 items-center rounded-full bg-raised px-4 font-medium text-text no-underline hover:bg-line"
              >
                {step.action}
              </Link>
            ) : null}
          </li>
        ))}
      </ul>
    </Card>
  )
}
