import { useEffect, useState, type ReactNode } from 'react'
import { useT } from '../../i18n/I18n'
import { Notice } from '../forms'
import { Button } from '../ui/button'
import { MusicWarning } from './MusicWarning'
import { loadSource, type Source } from './source'

export type ReadySource = Extract<Source, { status: 'ready' }>

/**
 * Asks MOWL, Spotify and Swedebeat what is on offer, and shows the loading, `no_mowl` and
 * "playlists failed" states the start screen and the advanced form share; `children` gets the
 * ready answer.
 */
export function SourceGate({
  children,
  onReady,
}: {
  children: (source: ReadySource) => ReactNode
  /** Told whether the answer is ready, so a page can hold back what only makes sense with it (the start aside). */
  onReady?: (ready: boolean) => void
}) {
  const t = useT()
  const [source, setSource] = useState<Source>({ status: 'loading' })
  const [reload, setReload] = useState(0)

  useEffect(() => {
    const abort = new AbortController()
    void loadSource(abort.signal).then((loaded) => {
      if (loaded !== null && !abort.signal.aborted) setSource(loaded)
    })
    return () => {
      abort.abort()
    }
  }, [reload])

  const ready = source.status === 'ready'
  useEffect(() => {
    onReady?.(ready)
  }, [ready, onReady])

  return (
    <>
      {source.status === 'loading' ? <p className="m-0 text-muted">{t('newclass.loading')}</p> : null}
      {source.status === 'no_mowl' ? (
        <MusicWarning reconnect={source.reconnect} />
      ) : null}
      {source.status === 'playlists_failed' ? (
        <>
          <Notice kind="error">{t(source.message)}</Notice>
          <p className="m-0">
            <Button
              variant="secondary"
              onClick={() => {
                setSource({ status: 'loading' })
                setReload((n) => n + 1)
              }}
            >
              {t('common.tryAgain')}
            </Button>
          </p>
        </>
      ) : null}
      {source.status === 'ready' ? children(source) : null}
    </>
  )
}
