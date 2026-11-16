import type { ClassSummary } from '../../api/classes'
import { useT } from '../../i18n/I18n'
import { startAgainPath } from '../../lib/startRequest'
import { Link } from '../../router/Router'
import { Notice } from '../forms'
import { Card, CardTitle } from '../ui/card'
import { StudioOutputs } from './StudioOutputs'
import { StudioTimeline } from './StudioTimeline'
import type { StudioView } from './useStudioTimeline'

/**
 * A studio class's draft on the class page: the music timeline and the output choice. Nothing is
 * shown for a class that is not a studio class. A draft whose cached analysis expired says so and
 * offers Change songs, which is a new studio pick (it costs a class credit) and then a design.
 */
export function StudioPanel({ summary, view }: { summary: ClassSummary; view: StudioView }) {
  const t = useT()
  if (view.status === 'loading') return null
  if (view.status === 'failed') return <Notice kind="error">{t('studio.tl.loadFailed')}</Notice>
  if (view.kind === 'none') return null
  if (view.kind === 'music_changed') {
    return (
      <Card className="flex flex-col gap-3" data-testid="studio-music-changed">
        <CardTitle>{t('studio.musicChanged.title')}</CardTitle>
        <p className="m-0 text-text">{t('studio.reason.music_changed')}</p>
        <p className="m-0">
          <Link className="button" to={startAgainPath(summary.id)}>
            {t('studio.changeSongs')}
          </Link>
        </p>
      </Card>
    )
  }
  // Only the class's current version can be approved: the server refuses another one.
  const current = view.timeline.revisionId === summary.currentRevisionId
  return (
    <Card className="flex flex-col gap-5">
      <StudioTimeline timeline={view.timeline} />
      {current ? <StudioOutputs classId={summary.id} timeline={view.timeline} disabled={summary.openJob !== null} /> : null}
    </Card>
  )
}
