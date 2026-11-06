import { Check, Circle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getConnections } from '../../api/connections'
import { getSpotifyStatus } from '../../api/spotify'
import { useT } from '../../i18n/I18n'
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
  const t = useT()
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
          { key: 'account', label: t('setup.account'), done: true },
          {
            key: 'mowl',
            label: mowl?.status === 'needs_reconnect' ? t('setup.mowlAgain') : t('setup.mowl'),
            done: mowl?.status === 'connected',
            to: '/account',
            action: t('setup.action'),
          },
        ]
        if (spotify !== null) {
          steps.push({
            key: 'spotify',
            label: spotify.status === 'needs_reconnect' ? t('setup.spotifyAgain') : t('setup.spotify'),
            done: spotify.status === 'connected',
            to: '/account/spotify',
            action: t('setup.action'),
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
  }, [t])

  if (state.status !== 'ready') return null
  return (
    <Card aria-labelledby="setup-title" role="region">
      <CardTitle id="setup-title">{t('setup.title')}</CardTitle>
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
              <span className="sr-only">{step.done ? t('setup.done') : t('setup.notDone')}</span>
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
