import { useEffect, useState } from 'react'
import { getConnections } from '../api/connections'
import { getMowlPlaylists } from '../api/jobs'
import { getClassPlaylists } from '../api/music'
import { getSpotifyStatus } from '../api/spotify'
import { useT } from '../i18n/I18n'
import { RequireAuth } from '../components/RequireAuth'
import { MadeCard, MowlEmptyState, MowlTiles } from '../components/music/MusicTiles'
import { SpotifyAttribution } from '../components/songs/SpotifyAttribution'
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
async function spotifyNeedsConnect(signal: AbortSignal): Promise<boolean> {
  const status = await getSpotifyStatus(signal)
  if (status === null) return false
  const connections = await getConnections()
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
  const connect = useLoad(spotifyNeedsConnect)
  const made = useLoad(getClassPlaylists)
  const mowl = useLoad(getMowlPlaylists)
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-10">
      <PageHeading eyebrow={t('music.page.eyebrow')} title={t('music.page.title')} intro={t('music.page.subtitle')} />
      {connect.status === 'ready' && connect.items ? (
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
          <>
            <ul className="m-0 grid list-none grid-cols-1 gap-5 p-0 sm:grid-cols-2 lg:grid-cols-3">
              {made.items.map((p) => (
                <li key={`${p.classId}-${p.name}`}>
                  <MadeCard playlist={p} />
                </li>
              ))}
            </ul>
            <SpotifyAttribution />
          </>
        ) : null}
      </section>
      <section aria-labelledby="mowl-title" className="flex flex-col gap-4">
        <PageHeading as="h2" size="section" titleId="mowl-title" title={t('music.page.mowlTitle')} />
        {mowl.status === 'loading' ? <p className="m-0 text-muted">{t('music.page.mowlLoading')}</p> : null}
        {mowl.status === 'failed' ? <MowlEmptyState /> : null}
        {mowl.status === 'ready' && mowl.items.length === 0 ? (
          <p className="m-0 text-muted">{t('music.page.mowlEmpty')}</p>
        ) : null}
        {mowl.status === 'ready' && mowl.items.length > 0 ? (
          <>
            <MowlTiles playlists={mowl.items} />
            <SpotifyAttribution />
          </>
        ) : null}
      </section>
    </div>
  )
}
