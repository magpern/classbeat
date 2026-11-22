import { studioReason } from '../../api/studioSongs'
import { useT } from '../../i18n/I18n'
import { minutes } from '../../lib/songs'
import { startAgainPath } from '../../lib/startRequest'
import { Link } from '../../router/Router'
import { Notice } from '../forms'
import { Button } from '../ui/button'
import type { PreviewAnswer } from './useMixPreview'

/**
 * The new total against the class length (ADR 0036 amendment B, B6), for every form, and the
 * refusal in words. A refusal never changes anything on its own: it offers **Re-plan the other
 * songs** (a model call the user starts) or **Change songs**, as `replanOffered` decides.
 */
export function StudioMixTotal({
  classId,
  answer,
  reason,
  replan,
  changeSongs,
  busy,
  onReplan,
}: {
  classId: string
  answer: PreviewAnswer
  /** The first reason these choices are refused (the preview's, or the one a job was refused with). */
  reason: string | null
  replan: boolean
  changeSongs: boolean
  busy: boolean
  onReplan: () => void
}) {
  const t = useT()
  if (answer.status === 'loading') {
    return (
      <p className="m-0 text-muted" role="status" data-testid="mix-total">
        {t('studio.mix.checking')}
      </p>
    )
  }
  const preview = answer.status === 'ready' ? answer.preview : null
  const total =
    preview === null || preview.totalSec === null
      ? null
      : t('studio.mix.total', {
          total: minutes(preview.totalSec),
          target: minutes(preview.targetSec),
          low: minutes(preview.allowed.lowSec),
          high: minutes(preview.allowed.highSec),
        })
  const words = reason === null ? null : (studioReason(reason) ?? t('studio.mix.refusedOther'))
  return (
    <div className="flex flex-col gap-2" data-testid="mix-total">
      {total !== null ? <p className="m-0 font-medium text-text">{total}</p> : null}
      {answer.status === 'failed' && words === null ? <Notice kind="error">{answer.message}</Notice> : null}
      {words !== null ? <Notice kind="error">{words}</Notice> : null}
      {replan || changeSongs ? (
        <div className="flex flex-wrap gap-2">
          {replan ? (
            <Button variant="secondary" disabled={busy} onClick={onReplan}>
              {t('studio.mix.replan')}
            </Button>
          ) : null}
          {changeSongs ? (
            <Link className="button" to={startAgainPath(classId)}>
              {t('studio.changeSongs')}
            </Link>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
