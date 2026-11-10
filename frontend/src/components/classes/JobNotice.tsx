import type { ClassSummary } from '../../api/classes'
import { useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { Notice } from '../forms'

/** The open job of a class, when it has its own page: the design of a class that has a version, a timing fix, the songs. */
export function JobNotice({ job }: { job: NonNullable<ClassSummary['openJob']> }) {
  const t = useT()
  const waiting = job.state === 'waiting_for_approval'
  return (
    <Notice>
      {waiting
        ? t('class.page.job.waiting')
        : job.kind === 'design_draft'
          ? t('class.page.job.design')
          : job.kind === 'refit_draft'
            ? t('class.page.job.refit')
            : t('class.page.job.songs')}{' '}
      <Link to={`/jobs/${job.id}`}>{waiting ? t('class.page.job.review') : t('class.page.job.progress')}</Link>
    </Notice>
  )
}
