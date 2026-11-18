import { useT } from '../../i18n/I18n'
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
  busy,
  error,
  onApprove,
}: {
  /** A chat message is pending. */
  holding: boolean
  /** The stored messages have not been read yet, so a pending one is not ruled out. */
  checking: boolean
  busy: boolean
  error: string | null
  onApprove: () => void
}) {
  const t = useT()
  return (
    <section
      aria-label={t('cw.approve.region')}
      data-testid="approve-bar"
      className="flex flex-col gap-3 rounded-lg border border-line bg-surface p-4"
    >
      <p className="m-0 text-[0.9375rem] text-muted">{t('cw.approve.text')}</p>
      {holding ? <p className="m-0 text-sm text-muted">{t('cw.approve.hold')}</p> : null}
      {checking && !holding ? <p className="m-0 text-sm text-muted">{t('cw.approve.checking')}</p> : null}
      {error !== null ? <Notice kind="error">{error}</Notice> : null}
      <div>
        <Button disabled={holding || checking || busy} onClick={onApprove}>
          {t('cw.approve.button')}
        </Button>
      </div>
    </section>
  )
}
