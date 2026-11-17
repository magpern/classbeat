import type { ClassState } from '../../hooks/useClassData'
import { useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { Card, Notice } from '../forms'
import { DiscardedClass } from './DiscardedClass'

/** The class page while there is no class to show: loading, not found, discarded (ADR 0039) or not loaded. */
export function ClassUnavailable({
  classId,
  state,
  onRetry,
}: {
  classId: string
  state: Exclude<ClassState, { status: 'ready' }>
  onRetry: () => void
}) {
  const t = useT()
  if (state.status === 'loading') {
    return (
      <Card title={t('class.page.loadingTitle')}>
        <p>{t('class.page.loading')}</p>
      </Card>
    )
  }
  if (state.status === 'discarded') return <DiscardedClass classId={classId} jobId={state.jobId} />
  if (state.status === 'not_found') {
    return (
      <Card title={t('class.page.notFoundTitle')}>
        <p>{t('class.page.notFound')}</p>
        <p>
          <Link to="/classes">{t('class.list.title')}</Link>
        </p>
      </Card>
    )
  }
  return (
    <Card title={t('class.page.failedTitle')}>
      <Notice kind="error">{t('class.page.failed')}</Notice>
      <p>
        <button type="button" className="button" onClick={onRetry}>
          {t('class.tryAgain')}
        </button>
      </p>
    </Card>
  )
}
