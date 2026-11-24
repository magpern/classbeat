import type { InterpretationItem } from '../../api/interpretationItems'
import { useT } from '../../i18n/I18n'
import { NotAskedBefore } from '../classes/NotAskedBefore'
import { Notice } from '../forms'
import { Button } from '../ui/button'

/**
 * The approve bar (ADR 0038 section 10.2): what the click does, and **Approve and save**. The text
 * is the ADR's exact copy. Approve waits while a chat message is pending, so the user never
 * approves something older than their last instruction (ADR section 8).
 */
export function ApproveBar({
  holding,
  checking,
  checkFailed,
  onRetryCheck,
  busy,
  error,
  onApprove,
  notAsked = [],
}: {
  /** A chat message is pending. */
  holding: boolean
  /** The stored messages have not been read yet, so a pending one is not ruled out. */
  checking: boolean
  /** The read of the stored messages failed: say so, and offer another. */
  checkFailed: boolean
  onRetryCheck: () => void
  busy: boolean
  error: string | null
  onApprove: () => void
  /** The preview's Not as asked items: shown before the click, so a difference is seen first. */
  notAsked?: readonly InterpretationItem[]
}) {
  const t = useT()
  return (
    <section
      aria-label={t('cw.approve.region')}
      data-testid="approve-bar"
      className="flex flex-col gap-3 rounded-lg border border-line bg-surface p-4"
    >
      <p className="m-0 text-[0.9375rem] text-muted">{t('cw.approve.text')}</p>
      <NotAskedBefore items={notAsked} before="approve" />
      {holding ? <p className="m-0 text-sm text-muted">{t('cw.approve.hold')}</p> : null}
      {checking && !checkFailed ? <p className="m-0 text-sm text-muted">{t('cw.approve.checking')}</p> : null}
      {checking && checkFailed ? (
        <p className="m-0 flex flex-wrap items-center gap-2 text-sm text-muted">
          {t('cw.approve.checkFailed')}
          <Button variant="secondary" className="min-h-11 px-3 text-[0.8125rem]" onClick={onRetryCheck}>
            {t('cw.approve.checkRetry')}
          </Button>
        </p>
      ) : null}
      {error !== null ? <Notice kind="error">{error}</Notice> : null}
      <div>
        <Button disabled={holding || checking || busy} onClick={onApprove}>
          {t('cw.approve.button')}
        </Button>
      </div>
    </section>
  )
}
