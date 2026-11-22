import { Pause, Play } from 'lucide-react'
import { useState } from 'react'
import { useSpotifyPreview } from '../../../hooks/editor/useSpotifyPreview'
import { useT } from '../../../i18n/I18n'
import { playTarget } from '../../../preview/spotifyTimelineModel'
import { Link } from '../../../router/Router'
import { SPOTIFY_PATH } from '../../account/connections/SpotifyRow'
import { Notice } from '../../forms'
import { Button } from '../../ui/button'
import { CorrectionPanel } from './CorrectionPanel'
import { LanePicker } from './LanePicker'
import { SpotifyStatus } from './SpotifyStatus'
import { SpotifyTimeline } from './SpotifyTimeline'

/**
 * Listening to a Spotify class on the person's own Spotify app (ADR 0042 section 7). It sits beside
 * the read-only notice: editing a Spotify class is not available yet, listening is. Nothing plays
 * until a click; the cursor is whatever the player was observed to do.
 */
export function SpotifyAudition({ classId }: { classId: string }) {
  const t = useT()
  const preview = useSpotifyPreview(classId)
  const [overlapAt, setOverlapAt] = useState<number | null>(null)
  const { load, view } = preview
  if (load.status === 'none') return null
  if (load.status === 'loading') return <p className="m-0" data-testid="spotify-loading">{t('preview.loading')}</p>
  if (load.status === 'failed' || view === null) {
    return (
      <div className="flex flex-col gap-2" data-testid="spotify-load-failed">
        <Notice kind="error">{t('preview.loadFailed')}</Notice>
        <div>
          <Button variant="secondary" onClick={preview.retry}>
            {t('editor.retry')}
          </Button>
        </div>
      </div>
    )
  }
  const { model, alignment, previewGranted } = load
  const playAt = (classMs: number, pick: 'incoming' | 'outgoing' = 'incoming') => {
    const target = playTarget(model, classMs, pick)
    if (target !== null) preview.play(target.piece, target.sourceMs, target.classMs)
    setOverlapAt(null)
  }
  const first = model.windows[0]
  const overlap = overlapAt === null ? undefined : model.overlaps.find((o) => overlapAt >= o.startMs && overlapAt <= o.endMs)
  const title = (piece: number): string => model.songs.find((s) => s.piece === piece)?.label ?? ''
  return (
    <section className="flex flex-col gap-3" aria-label={t('preview.title')} data-testid="spotify-audition">
      <h2 className="m-0 font-display text-xl font-bold">{t('preview.title')}</h2>
      <p className="m-0 text-muted">{t('preview.intro')}</p>
      {previewGranted ? null : (
        <div className="flex flex-col gap-2" data-testid="spotify-not-granted">
          <Notice>{t('preview.err.preview_not_granted')}</Notice>
          <p className="m-0">
            <Link className="button" to={SPOTIFY_PATH} data-testid="spotify-reconnect">
              {t('preview.action.reconnect')}
            </Link>
          </p>
        </div>
      )}
      <p className="m-0 flex flex-wrap gap-2">
        <Button disabled={first === undefined || !previewGranted} onClick={() => first !== undefined && playAt(first.startMs)} data-testid="spotify-play">
          <Play aria-hidden="true" className="size-4" />
          {t('preview.play')}
        </Button>
        <Button variant="secondary" disabled={view.state.status !== 'playing'} onClick={preview.pause} data-testid="spotify-pause">
          <Pause aria-hidden="true" className="size-4" />
          {t('preview.pause')}
        </Button>
      </p>
      <SpotifyStatus view={view} onReload={preview.retry} />
      <SpotifyTimeline model={model} view={view} onPlayAt={(ms) => playAt(ms)} onOverlap={setOverlapAt} />
      {overlap === undefined || overlapAt === null ? null : (
        <LanePicker incomingTitle={title(overlap.incoming)} outgoingTitle={title(overlap.outgoing)} onPick={(pick) => playAt(overlapAt, pick)} onCancel={() => setOverlapAt(null)} />
      )}
      <CorrectionPanel alignment={alignment} initialPiece={view.state.piece ?? null} saving={preview.saving} failure={preview.correctionFailure} onCorrect={preview.correct} />
    </section>
  )
}
