import type { ClassSummary } from '../../api/classes'
import { useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { Notice } from '../forms'

/**
 * The open job of a class, when it has its own progress page: the design of a class that has a version,
 * or a timing fix. A song job is shown in place on the class page, a save has its own panel, and a revise
 * or a reading shows in the chat: for those the notice says nothing rather than name the wrong work.
 */
export function JobNotice({ job }: { job: NonNullable<ClassSummary['openJob']> }) {
  const t = useT()
  if (job.kind !== 'design_draft' && job.kind !== 'refit_draft') return null
  return (
    <Notice>
      {job.kind === 'design_draft' ? t('class.page.job.design') : t('class.page.job.refit')}{' '}
      <Link to={`/jobs/${job.id}`}>{t('class.page.job.progress')}</Link>
    </Notice>
  )
}
