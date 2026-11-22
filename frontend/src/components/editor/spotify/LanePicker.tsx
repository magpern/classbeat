import { useT } from '../../../i18n/I18n'
import { Button } from '../../ui/button'

interface Props {
  incomingTitle: string
  outgoingTitle: string
  onPick: (pick: 'incoming' | 'outgoing') => void
  onCancel: () => void
}

/** Inside an overlap two songs play in the class: the person picks the incoming one (the default) or the outgoing one. */
export function LanePicker({ incomingTitle, outgoingTitle, onPick, onCancel }: Props) {
  const t = useT()
  return (
    <div className="flex flex-col gap-2 rounded-md border border-line bg-surface p-3" role="group" aria-label={t('preview.lane.title')} data-testid="lane-picker">
      <p className="m-0 font-medium text-text">{t('preview.lane.title')}</p>
      <div className="flex flex-wrap gap-2">
        <Button autoFocus onClick={() => onPick('incoming')} data-testid="lane-incoming">
          {t('preview.lane.incoming', { title: incomingTitle })}
        </Button>
        <Button variant="secondary" onClick={() => onPick('outgoing')} data-testid="lane-outgoing">
          {t('preview.lane.outgoing', { title: outgoingTitle })}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          {t('preview.lane.cancel')}
        </Button>
      </div>
    </div>
  )
}
