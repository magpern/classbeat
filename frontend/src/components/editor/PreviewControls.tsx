import { Pause, Play } from 'lucide-react'
import { useT } from '../../i18n/I18n'
import { playMessage } from '../../lib/editor/playMessage'
import type { TransportState } from '../../preview/transport'
import { Notice } from '../forms'
import { Button } from '../ui/button'

/**
 * Play and Pause for the rendered preview, and what it says in words: a wait, the 15-minute
 * pause, an ended session or an error with Try again. Play is always an explicit press.
 */
export function PreviewControls({ state, onPlay, onPause }: { state: TransportState; onPlay: () => void; onPause: () => void }) {
  const t = useT()
  const message = playMessage(state)
  const running = state.status === 'playing' || state.status === 'starting'
  return (
    <div className="flex flex-col gap-2" role="group" aria-label={t('editor.play.label')} data-testid="preview-controls">
      <div className="flex flex-wrap items-center gap-3">
        {running ? (
          <Button onClick={onPause}>
            <Pause aria-hidden="true" className="size-4" />
            {t('editor.play.pause')}
          </Button>
        ) : (
          <Button onClick={onPlay}>
            <Play aria-hidden="true" className="size-4" />
            {t(message?.retry === true ? 'editor.play.retry' : 'editor.play.play')}
          </Button>
        )}
        <p className="m-0 text-[0.9375rem] text-muted">{t('editor.play.approx')}</p>
      </div>
      <div data-testid="preview-status">
        {message === null ? null : <Notice kind={message.tone === 'error' ? 'error' : undefined}>{t(message.key)}</Notice>}
      </div>
    </div>
  )
}
