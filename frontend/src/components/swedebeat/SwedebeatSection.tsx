import { useEffect, useState } from 'react'
import type { ClassSummary } from '../../api/classes'
import { getConnections, getConnectionsView, type Connection } from '../../api/connections'
import { createMatchJob, getSwedebeatMatch, swedebeatError, needsReconnect, type SwedebeatMatch } from '../../api/swedebeatMatch'
import { isFeatureOff } from '../../api/spotify'
import { actionKey, dropActionKey } from '../../lib/submittedInput'
import { Link, useRouter } from '../../router/Router'
import { Notice, useAction } from '../forms'
import { Button } from '../ui/button'
import { Card, CardTitle } from '../ui/card'
import { MatchCard } from './MatchCard'
import { NextStep } from './NextStep'
import { ReconnectPrompt } from './ReconnectPrompt'

/**
 * The Swedebeat part of the class page (ADR 0016, B4). Nothing shows while the
 * `swedebeat_enabled` flag is off (the class answer then has no match key at all).
 */
export function SwedebeatSection({ summary, onReload }: { summary: ClassSummary; onReload: () => void }) {
  if (!summary.swedebeatOn) return null
  return summary.swedebeatMatch === null ? (
    <StartMatch summary={summary} />
  ) : (
    <MatchSection summary={summary} pointer={summary.swedebeatMatch} onReload={onReload} />
  )
}

function useSwedebeatConnection(): Connection | null | undefined {
  const [found, setFound] = useState<Connection | null | undefined>(undefined)
  useEffect(() => {
    let live = true
    getConnections().then(
      (all) => {
        if (live) setFound(all.find((c) => c.provider === 'swedebeat') ?? null)
      },
      () => {
        if (live) setFound(null)
      },
    )
    return () => {
      live = false
    }
  }, [])
  return found
}

/**
 * Whether this user said "I use Swedebeat" (#525) and their connection; `undefined` while loading.
 * Starting a match is offered only to such a user; a match that already exists still shows.
 */
function useStartChoice(): { uses: boolean; connection: Connection | null } | undefined {
  const [found, setFound] = useState<{ uses: boolean; connection: Connection | null } | undefined>(undefined)
  useEffect(() => {
    let live = true
    getConnectionsView().then(
      (all) => {
        if (live) {
          setFound({
            uses: all.swedebeat.usesSwedebeat,
            connection: all.connections.find((c) => c.provider === 'swedebeat') ?? null,
          })
        }
      },
      () => {
        if (live) setFound({ uses: false, connection: null })
      },
    )
    return () => {
      live = false
    }
  }, [])
  return found
}

function StartMatch({ summary }: { summary: ClassSummary }) {
  const choice = useStartChoice()
  const connection = choice?.connection
  const action = useAction()
  const { navigate } = useRouter()
  const [reconnect, setReconnect] = useState(false)
  const listId = summary.songs.state === 'approved' ? summary.songs.revisionId : null
  if (listId === null || choice === undefined || !choice.uses) return null
  const ready = connection?.status === 'connected'

  const start = () => {
    if (connection === null || connection === undefined) return
    const scope = `swedebeat-match.${summary.id}.${listId}`
    void action.run(
      async () => {
        const job = await createMatchJob(
          { class_id: summary.id, song_list_revision_id: listId, swedebeat_connection_id: connection.id },
          actionKey(scope),
        )
        dropActionKey(scope)
        navigate(`/jobs/${job.id}`)
      },
      (error) => {
        if (needsReconnect(error)) setReconnect(true)
        return swedebeatError(error)
      },
    )
  }

  return (
    <Card role="region" aria-labelledby="swedebeat-start-title" className="flex flex-col gap-3">
      <CardTitle id="swedebeat-start-title">Swedebeat</CardTitle>
      <p className="m-0 text-muted">
        See which of these songs Swedebeat has, so you can build the mix there. Only titles and artists are
        looked up, and nothing is written to your Swedebeat account.
      </p>
      {connection === null ? (
        <p className="m-0">
          <Link to="/account#connections">Connect Swedebeat on your account page first</Link>
        </p>
      ) : null}
      {connection?.status === 'needs_reconnect' || reconnect ? <ReconnectPrompt /> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <div>
        <Button disabled={!ready || action.busy || summary.openJob !== null} onClick={start}>
          Match with Swedebeat
        </Button>
      </div>
    </Card>
  )
}

type Loaded = { id: string; match: SwedebeatMatch } | 'failed' | 'off'

function MatchSection({
  summary,
  pointer,
  onReload,
}: {
  summary: ClassSummary
  pointer: { revisionId: string; approved: boolean }
  onReload: () => void
}) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const connection = useSwedebeatConnection()
  useEffect(() => {
    const abort = new AbortController()
    getSwedebeatMatch(summary.id, pointer.revisionId, abort.signal).then(
      (match) => {
        if (!abort.signal.aborted) setLoaded({ id: pointer.revisionId, match })
      },
      (error: unknown) => {
        if (abort.signal.aborted) return
        setLoaded(isFeatureOff(error) ? 'off' : 'failed')
      },
    )
    return () => {
      abort.abort()
    }
  }, [summary.id, pointer.revisionId])

  if (loaded === 'off') return null
  if (loaded === 'failed') {
    return <Notice kind="error">The Swedebeat match could not be loaded. Reload the page.</Notice>
  }
  if (loaded === null || loaded.id !== pointer.revisionId) {
    return <p className="m-0 text-muted">Loading the Swedebeat match</p>
  }
  return (
    <>
      {connection?.status === 'needs_reconnect' ? <ReconnectPrompt /> : null}
      <MatchCard summary={summary} match={loaded.match} approved={pointer.approved} onReload={onReload} />
      {pointer.approved ? <NextStep summary={summary} match={loaded.match} onReload={onReload} /> : null}
    </>
  )
}
