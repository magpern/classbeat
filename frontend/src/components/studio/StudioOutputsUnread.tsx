import { useT } from '../../i18n/I18n'
import { Notice } from '../forms'
import { Button } from '../ui/button'

/** The outputs of the class could not be read: say so, with a retry (the choice and the states wait for it). */
export function StudioOutputsUnread({ onRetry }: { onRetry: () => void }) {
  const t = useT()
  return (
    <div className="flex flex-col items-start gap-2" data-testid="studio-outputs-unread">
      <Notice kind="error">{t('studio.out.loadFailed')}</Notice>
      <Button type="button" variant="secondary" onClick={onRetry}>
        {t('studio.out.retry')}
      </Button>
    </div>
  )
}
