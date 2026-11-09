import { useEffect, useState } from 'react'
import { getConnections } from '../../api/connections'
import { getSpotifyStatus } from '../../api/spotify'
import { useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { ConnectionRow } from '../account/connections/ConnectionRow'
import { Button } from '../ui/button'

interface Step {
  key: 'mowl' | 'spotify'
  mark: string
  name: string
  optional: boolean
  status: string
  to: string
}

type State = { status: 'loading' } | { status: 'ready'; steps: Step[] } | { status: 'hidden' }

/**
 * "Get set up": the connections still missing, as rows with one Connect button each: MOWL and,
 * where song picking is on, Spotify (optional). Built from the existing connection APIs and shown
 * until every one is connected. A failed read hides it rather than guessing.
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
        const steps: Step[] = []
        if (mowl?.status !== 'connected') {
          steps.push({
            key: 'mowl',
            mark: 'M',
            name: t('conn.mowl.name'),
            optional: false,
            status: mowl?.status === 'needs_reconnect' ? t('mowl.statusAgain') : t('conn.mowl.purpose'),
            to: '/account',
          })
        }
        if (spotify !== null && spotify.status !== 'connected') {
          steps.push({
            key: 'spotify',
            mark: 'S',
            name: t('spot.title'),
            optional: true,
            status: spotify.status === 'needs_reconnect' ? t('mowl.statusAgain') : t('conn.spotify.purpose'),
            to: '/account/spotify',
          })
        }
        setState(steps.length === 0 ? { status: 'hidden' } : { status: 'ready', steps })
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
    <section aria-labelledby="setup-title" className="flex flex-col gap-3">
      <h2 id="setup-title" className="m-0 font-display text-xl font-bold">
        {t('setup.title')}
      </h2>
      <ul className="m-0 flex list-none flex-col divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface p-0">
        {state.steps.map((step) => (
          <ConnectionRow
            key={step.key}
            testId={`setup-${step.key}`}
            mark={step.mark}
            name={step.name}
            optional={step.optional}
            status={step.status}
            // On a narrow phone the text keeps at least 8rem and the button drops to its own line.
            className="flex-wrap sm:flex-nowrap [&>div:nth-child(2)]:min-w-32 [&>div:nth-child(2)]:basis-32"
            action={
              <div className="w-full sm:w-auto">
                <Button asChild variant="secondary">
                  <Link to={step.to} aria-label={t('setup.connectNamed', { name: step.name })}>
                    {t('conn.connect')}
                  </Link>
                </Button>
              </div>
            }
          />
        ))}
      </ul>
    </section>
  )
}
