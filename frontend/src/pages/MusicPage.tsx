import { useEffect, useState } from 'react'
import { getConnections, type Connection } from '../api/connections'
import { getMowlPlaylists } from '../api/jobs'
import { getClassPlaylists } from '../api/music'
import { getSpotifyStatus, type SpotifyStatus } from '../api/spotify'
import { useT } from '../i18n/I18n'
import { RequireAuth } from '../components/RequireAuth'
import { MadeCard, MowlEmptyState, MowlTiles } from '../components/music/MusicTiles'
import { SpotifyAttributionPill } from '../components/music/SpotifyAttributionPill'
import { Button } from '../components/ui/button'
import { Card, CardContent, CardTitle } from '../components/ui/card'
import { PageHeading } from '../components/ui/page-heading'
import { Link } from '../router/Router'

type Load<T> = { status: 'loading' } | { status: 'failed' } | { status: 'ready'; items: T }

function useLoad<T>(read: (signal: AbortSignal) => Promise<T>): Load<T> {
  const [state, setState] = useState<Load<T>>({ status: 'loading' })
  useEffect(() => {
    const abort = new AbortController()
    read(abort.signal).then(
      (items) => {
        if (!abort.signal.aborted) setState({ status: 'ready', items })
      },
      () => {
        if (!abort.signal.aborted) setState({ status: 'failed' })
      },
    )
    return () => {
      abort.abort()
    }
    // `read` is a module-level function: one read per mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return state
}

/** Whether to offer "Connect Spotify": only when song picking is on and Spotify is not connected. */
function needsSpotifyConnect(status: SpotifyStatus | null, connections: readonly Connection[]): boolean {
  if (status === null) return false
  const spotify = connections.find((c) => c.provider === 'spotify')
  return spotify === undefined || spotify.status !== 'connected' || status.status !== 'connected'
}

/** Music (ADR 0013 sections 4 and 9): the MOWL playlists as tiles and the ones classbeat made. */
export function MusicPage() {
  return (
    <RequireAuth>
      <Music />
    </RequireAuth>
  )
}

function Music() {
  const t = useT()
  const connections = useLoad(getConnections) // read once: the connect card and the MOWL state share it
  const spotify = useLoad(getSpotifyStatus)
  const made = useLoad(getClassPlaylists)
  const mowl = useLoad(getMowlPlaylists)
  const mowlOn = connections
  // A failed read counts as not connected.
  const connected =
    connections.status === 'ready' && connections.items.some((c) => c.provider === 'mowl' && c.status === 'connected')
  const showConnect =
    spotify.status === 'ready' &&
    connections.status === 'ready' &&
    needsSpotifyConnect(spotify.items, connections.items)
  // The logo is shown only when the page lists Spotify content: not while loading, not when both lists are empty or failed.
  const showsSpotifyContent =
    (made.status === 'ready' && made.items.length > 0) || (mowl.status === 'ready' && mowl.items.length > 0)
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-10">
      <div className="flex flex-col items-start gap-4">
        <PageHeading eyebrow={t('music.page.eyebrow')} title={t('music.page.title')} intro={t('music.page.subtitle')} />
        {showsSpotifyContent ? <SpotifyAttributionPill /> : null}
      </div>
      {showConnect ? (
        <Card role="region" aria-labelledby="connect-title" className="flex flex-col gap-3">
          <CardTitle id="connect-title" className="mb-0">
            {t('music.page.connectTitle')}
          </CardTitle>
          <CardContent>{t('music.page.connectText')}</CardContent>
          <div>
            <Button asChild>
              <Link to="/account/spotify">{t('music.page.connectButton')}</Link>
            </Button>
          </div>
        </Card>
      ) : null}
      <section aria-labelledby="made-title" className="flex flex-col gap-4">
        <PageHeading as="h2" size="section" titleId="made-title" title={t('music.page.madeTitle')} />
        {made.status === 'loading' ? <p className="m-0 text-muted">{t('music.page.madeLoading')}</p> : null}
        {made.status === 'failed' ? <p className="m-0 text-muted">{t('music.page.madeFailed')}</p> : null}
        {made.status === 'ready' && made.items.length === 0 ? (
          <p className="m-0 text-muted">{t('music.page.madeEmpty')}</p>
        ) : null}
        {made.status === 'ready' && made.items.length > 0 ? (
          <ul className="m-0 grid list-none grid-cols-1 gap-5 p-0 sm:grid-cols-2 lg:grid-cols-3">
            {made.items.map((p) => (
              <li key={`${p.classId}-${p.name}`}>
                <MadeCard playlist={p} />
              </li>
            ))}
          </ul>
        ) : null}
      </section>
      <section aria-labelledby="mowl-title" className="flex flex-col gap-4">
        <PageHeading as="h2" size="section" titleId="mowl-title" title={t('music.page.mowlTitle')} />
        {mowl.status === 'loading' ? <p className="m-0 text-muted">{t('music.page.mowlLoading')}</p> : null}
        {mowl.status === 'failed' && mowlOn.status !== 'loading' ? <MowlEmptyState connected={connected} /> : null}
        {mowl.status === 'ready' && mowl.items.length === 0 && mowlOn.status !== 'loading' ? (
          <MowlEmptyState connected={connected} empty />
        ) : null}
        {mowl.status === 'ready' && mowl.items.length > 0 ? <MowlTiles playlists={mowl.items} /> : null}
      </section>
    </div>
  )
}
