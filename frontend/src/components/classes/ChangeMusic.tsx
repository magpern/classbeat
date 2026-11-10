import { useState } from 'react'
import type { ClassSummary } from '../../api/classes'
import { useT } from '../../i18n/I18n'
import { recallRequest, rememberRequest, startAgainPath } from '../../lib/startRequest'
import { useRouter } from '../../router/Router'
import { SourceGate } from '../newclass/SourceGate'
import { MusicSourcePicker } from '../start/MusicSourcePicker'
import { musicRows } from '../start/musicSources'
import { Button } from '../ui/button'
import { Card, CardTitle } from '../ui/card'

/**
 * Change music from the workspace (MA4). It opens the same source-labelled picker as the start
 * screen, and then uses the paths that exist: for a class whose songs classbeat picked, "classbeat
 * picks songs" leads to Change songs on this page; any other source is a different design, so the
 * request and the choice are kept on the start screen and this class stays as it is. Nothing is
 * started or written from here, and the rows say where music comes from, not how it sounds.
 */
export function ChangeMusic({
  summary,
  request,
  canChangeSongs,
  onChangeSongs,
}: {
  summary: ClassSummary
  /** The words the class was designed from, when it was made on the start screen. */
  request: string | null
  canChangeSongs: boolean
  onChangeSongs: () => void
}) {
  const t = useT()
  const [open, setOpen] = useState(false)
  if (summary.currentRevisionId === null) return null
  return (
    <Card role="region" aria-labelledby="change-music-title" className="flex flex-col gap-3" data-testid="change-music">
      <CardTitle id="change-music-title">{t('music.change.title')}</CardTitle>
      {summary.openJob !== null ? <p className="m-0 text-muted">{t('music.change.wait')}</p> : null}
      {open ? (
        <SourceGate>
          {(source) => (
            <Chooser
              summary={summary}
              request={request}
              canChangeSongs={canChangeSongs}
              onChangeSongs={onChangeSongs}
              rows={musicRows(t, {
                playlists: source.playlists,
                mixes: source.mixes,
                songs: source.songs,
                swedebeat: source.swedebeat,
                soundlessEnabled: source.soundlessEnabled,
                running: summary.sport === 'running',
              })}
            />
          )}
        </SourceGate>
      ) : (
        <div>
          <Button
            variant="secondary"
            disabled={summary.openJob !== null}
            onClick={() => {
              setOpen(true)
            }}
          >
            {t('music.change.open')}
          </Button>
        </div>
      )}
    </Card>
  )
}

function Chooser({
  summary,
  request,
  canChangeSongs,
  onChangeSongs,
  rows,
}: {
  summary: ClassSummary
  request: string | null
  canChangeSongs: boolean
  onChangeSongs: () => void
  rows: ReturnType<typeof musicRows>
}) {
  const t = useT()
  const { navigate } = useRouter()
  const [key, setKey] = useState<string | null>(null)
  const chosen = rows.find((row) => row.key === key) ?? null
  const here = chosen?.key === 'pick' && canChangeSongs
  const startAgain = () => {
    if (chosen === null) return
    const kept = recallRequest(summary.id)
    rememberRequest(summary.id, {
      request: kept?.request ?? request ?? '',
      music: chosen.key,
      sport: summary.sport,
    })
    navigate(startAgainPath(summary.id))
  }
  return (
    <div className="flex flex-col gap-3">
      <MusicSourcePicker rows={rows} value={chosen?.key ?? null} onChange={setKey} />
      {chosen !== null ? (
        <>
          <p className="m-0" data-testid="change-music-next">
            {here ? t('music.change.here') : t('music.change.newDesign')}
          </p>
          <div>
            <Button onClick={here ? onChangeSongs : startAgain}>
              {here ? t('music.change.goSongs') : t('music.change.startAgain')}
            </Button>
          </div>
        </>
      ) : null}
    </div>
  )
}
