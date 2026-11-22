import { useT } from '../../i18n/I18n'
import type { Outcome } from '../../hooks/editor/useClassEditor'
import { Link } from '../../router/Router'
import { Notice } from '../forms'
import { Button } from '../ui/button'
import { reasonKey } from './reasons'

interface Props {
  classId: string
  outcome: Outcome
  busy: boolean
  onReapply: () => void
  onDismiss: () => void
}

/**
 * What the last edit left to say. A stale answer (the class moved under the page) has reloaded the
 * page and kept the unsent edit; it is offered again only if it still means the same block.
 */
export function EditOutcome({ classId, outcome, busy, onReapply, onDismiss }: Props) {
  const t = useT()
  if (outcome === null) return null
  if (outcome.kind === 'failed') return <Notice kind="error">{t('editor.outcome.failed')}</Notice>
  if (outcome.kind === 'refused') {
    return (
      <div className="flex flex-col gap-2">
        <Notice kind="error">{t(reasonKey(outcome.reason))}</Notice>
        {outcome.reason === 'mix_not_applied' ? (
          <p className="m-0">
            <Link className="button" to={`/classes/${classId}`}>
              {t('editor.readonly.back')}
            </Link>
          </p>
        ) : null}
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-2" data-testid="stale-notice">
      <Notice kind="error">{t('editor.stale.reloaded')}</Notice>
      {outcome.pending === null ? null : outcome.applies ? (
        <div className="flex flex-wrap items-center gap-2">
          <p className="m-0 text-[0.9375rem] text-text">{t('editor.stale.kept')}</p>
          <Button disabled={busy} onClick={onReapply}>
            {t('editor.stale.reapply')}
          </Button>
          <Button variant="ghost" onClick={onDismiss}>
            {t('editor.stale.drop')}
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <p className="m-0 text-[0.9375rem] text-text">{t('editor.stale.moved')}</p>
          <Button variant="ghost" onClick={onDismiss}>
            {t('editor.stale.drop')}
          </Button>
        </div>
      )}
    </div>
  )
}
