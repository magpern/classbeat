import { useEffect, useState } from 'react'
import { getConnections } from '../api/connections'
import { getMowlPlaylists, type Playlist } from '../api/jobs'
import { getClassPlaylists, type MadePlaylist } from '../api/music'
import { getSpotifyStatus } from '../api/spotify'
import { Trans, useT } from '../i18n/I18n'
import { RequireAuth } from '../components/RequireAuth'
import { CoverMosaic, initials } from '../components/songs/CoverArt'
import { SpotifyAttribution } from '../components/songs/SpotifyAttribution'
import { Button } from '../components/ui/button'
import { Card, CardContent, CardTitle } from '../components/ui/card'
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
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-6">
      <h1 className="m-0 font-display text-3xl font-extrabold">{t('music.page.title')}</h1>
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
      <section aria-labelledby="made-title" className="flex flex-col gap-3">
        <h2 id="made-title" className="m-0 font-display text-xl font-bold">
          {t('music.page.madeTitle')}
        </h2>
        {made.status === 'loading' ? <p className="m-0 text-muted">{t('music.page.madeLoading')}</p> : null}
        {made.status === 'failed' ? <p className="m-0 text-muted">{t('music.page.madeFailed')}</p> : null}
        {made.status === 'ready' && made.items.length === 0 ? (
          <p className="m-0 text-muted">{t('music.page.madeEmpty')}</p>
        ) : null}
        {made.status === 'ready' && made.items.length > 0 ? (
          <>
            <ul className="m-0 grid list-none grid-cols-1 gap-4 p-0 sm:grid-cols-2 lg:grid-cols-3">
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
      <section aria-labelledby="mowl-title" className="flex flex-col gap-3">
        <h2 id="mowl-title" className="m-0 font-display text-xl font-bold">
          {t('music.page.mowlTitle')}
        </h2>
        {mowl.status === 'loading' ? <p className="m-0 text-muted">{t('music.page.mowlLoading')}</p> : null}
        {mowl.status === 'failed' ? (
          <p className="m-0 text-muted">
            <Trans
              id="music.page.mowlFailed"
              slots={{ link: <Link to="/account">{t('music.page.mowlFailedLink')}</Link> }}
            />
          </p>
        ) : null}
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

function MowlTiles({ playlists }: { playlists: Playlist[] }) {
  const t = useT()
  return (
    <ul className="m-0 grid list-none grid-cols-2 gap-3 p-0 sm:grid-cols-3 lg:grid-cols-5">
      {playlists.map((p) => {
        const name = p.name || t('music.page.untitled')
        return (
          <li key={p.spotifyPlaylistId} className="flex flex-col gap-2 rounded-md border border-line bg-surface p-3">
            <span
              aria-hidden="true"
              className="flex aspect-square w-full items-center justify-center rounded-sm bg-raised font-display text-2xl font-extrabold text-muted"
            >
              {initials(name)}
            </span>
            <span className="break-words text-[0.9375rem] font-medium text-text">{name}</span>
          </li>
        )
      })}
    </ul>
  )
}

function MadeCard({ playlist }: { playlist: MadePlaylist }) {
  const t = useT()
  return (
    <Card className="flex flex-col gap-3 p-4">
      <CoverMosaic songs={playlist.songs} className="size-40 self-center" />
      <h3 className="m-0 break-words font-display text-lg font-bold">{playlist.name}</h3>
      <p className="m-0 text-[0.9375rem] text-muted">
        <Trans
          id="music.page.forClass"
          slots={{ link: <Link to={`/classes/${playlist.classId}`}>{playlist.classTitle}</Link> }}
        />
      </p>
      {playlist.note !== null ? <p className="m-0 text-[0.8125rem] text-muted">{playlist.note}</p> : null}
      {playlist.link !== null ? (
        <Button asChild variant="secondary" className="self-start">
          <a href={playlist.link} target="_blank" rel="noopener noreferrer">
            {t('music.page.openSpotify')}
          </a>
        </Button>
      ) : null}
    </Card>
  )
}
