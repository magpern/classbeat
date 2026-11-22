import type { ClassSummary } from '../../api/classes'
import type { ReadOnlyReason } from '../../hooks/editor/loadEditor'
import { useT } from '../../i18n/I18n'
import { startAgainPath } from '../../lib/startRequest'
import { Link } from '../../router/Router'
import { ProfileChart } from '../classes/ProfileChart'
import { Notice } from '../forms'
import { Button } from '../ui/button'

/** The class's zones as one segment, for the read-only chart (the editor's blocks are not available). */
function segmentsOf(summary: ClassSummary) {
  return [{ name: '', kind: 'main', intervals: summary.profile.map(([zone, sec]) => ({ sec, zone, rpm: 'beat' as const, standing: false, cue: '' })) }]
}

/**
 * The page when the draft cannot be edited: a clear sentence for the reason, the way forward that
 * exists, and the class's profile as it stands. Never a broken editor (an edit would be refused).
 */
export function EditorReadOnly({ reason, summary, onReload }: { reason: ReadOnlyReason; summary: ClassSummary; onReload: () => void }) {
  const t = useT()
  return (
    <div className="flex flex-col gap-4" data-testid={`readonly-${reason}`}>
      <Notice>{t(`editor.readonly.${reason}`)}</Notice>
      <p className="m-0 flex flex-wrap gap-2">
        {reason === 'open_job' ? <Button onClick={onReload}>{t('editor.readonly.reload')}</Button> : null}
        {reason === 'music_changed' ? (
          <Link className="button" to={startAgainPath(summary.id)}>
            {t('studio.changeSongs')}
          </Link>
        ) : null}
        <Link className="button" to={`/classes/${summary.id}`}>
          {t('editor.readonly.back')}
        </Link>
      </p>
      {summary.profile.length > 0 ? <ProfileChart segments={segmentsOf(summary)} title={summary.title} songs={null} /> : null}
    </div>
  )
}
