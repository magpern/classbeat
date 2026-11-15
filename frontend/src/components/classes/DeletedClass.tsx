import type { Deleted } from '../../api/classes'
import { useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { Card, Notice } from '../forms'
import { BeatBars } from '../ui/beat-bars'
import { isTerminal } from '../../api/jobs'
import { playerFailureText } from '../../api/playerMessages'
import { useJobProgress } from '../../hooks/useJobProgress'

/** The removal job a class's deletion started on the player, followed to its end (ADR 0034 section 5). */
function PlayerRemoval({ jobId }: { jobId: string }) {
  const t = useT()
  const { job, problem } = useJobProgress(jobId)
  if (job !== null && isTerminal(job.state)) {
    if (job.state === 'completed') return <Notice kind="success">{t('player.write.outcome.remove.found')}</Notice>
    const text =
      job.errorCategory === 'uncertain_write' ? t('player.remove.uncertain') : playerFailureText(job.errorCategory)
    return <Notice kind="error">{text}</Notice>
  }
  if (problem !== null) return <Notice kind="error">{t('player.remove.uncertain')}</Notice>
  return (
    <p role="status" data-testid="player-removal">
      <BeatBars />
      {t('player.remove.run')}
    </p>
  )
}


/** The page after a class was deleted: what stays in MOWL, and the player's removal while it runs. */
export default function DeletedClass({ deleted }: { deleted: Deleted }) {
  const t = useT()
  const kept = deleted.mowlWorkoutsKept
  return (
    <Card title={t('class.page.deletedTitle')}>
      <p>{t('class.page.deleted')}</p>
      {kept > 0 ? <p>{t('class.delete.kept', { count: kept })}</p> : null}
      {deleted.playerRemovalJobId !== null ? (
        <PlayerRemoval jobId={deleted.playerRemovalJobId} />
      ) : null}
      <p>
        <Link className="button" to="/classes">
          {t('class.list.title')}
        </Link>
      </p>
    </Card>
  )
}
