import { useEffect, useState, type ReactNode } from 'react'
import { Trans, useT } from '../../i18n/I18n'
import { Notice } from '../forms'
import { Button } from '../ui/button'
import { Link } from '../../router/Router'
import { loadSource, type Source } from './source'

export type ReadySource = Extract<Source, { status: 'ready' }>

/**
 * Asks MOWL, Spotify and Swedebeat what is on offer, and shows the loading, `no_mowl` and
 * "playlists failed" states the start screen and the advanced form share; `children` gets the
 * ready answer.
 */
export function SourceGate({ children }: { children: (source: ReadySource) => ReactNode }) {
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

  return (
    <>
      {source.status === 'loading' ? <p className="m-0 text-muted">{t('newclass.loading')}</p> : null}
      {source.status === 'no_mowl' ? (
        <Notice>
          <Trans
            id={source.reconnect ? 'newclass.noMowl.reconnect' : 'newclass.noMowl.connect'}
            slots={{
              link: (
                <Link to="/account">
                  {t(source.reconnect ? 'newclass.noMowl.reconnectLink' : 'newclass.noMowl.connectLink')}
                </Link>
              ),
            }}
          />
        </Notice>
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
