import { useEffect, useRef } from 'react'
import { useT } from '../../i18n/I18n'
import { Notice } from '../forms'
import { Button } from '../ui/button'
import { ConfirmButtons, Outcome, WriteRun } from './SaveProgress'
import type { SaveFlow } from './useSaveFlow'

/**
 * Save to MOWL and Update saved, drawn in the class header (ADR 0028 section 2: the primary action
 * is not buried). The state is `useSaveFlow`; this file is the three places it shows: the buttons
 * (`SaveActions`), the line that says what MOWL holds (`SaveStatus`) and the notices under the
 * header (`SaveNotices`: the confirmation, the progress and the outcome). The writes are asked first.
 */
export function SaveActions({ flow }: { flow: SaveFlow }) {
  const t = useT()
  if (flow.mode !== null) return null
  return (
    <div className="flex flex-wrap items-center gap-2 lg:order-last">
      <Button
        disabled={!flow.canWrite}
        onClick={() => {
          flow.setMode('save')
        }}
      >
        {flow.again === 'save'
          ? t('class.save.again')
          : flow.hasSaved
            ? t('class.save.asNew')
            : t('class.save.title')}
      </Button>
      {flow.showUpdate ? (
        <Button
          variant="secondary"
          disabled={!flow.canWrite || flow.savedId === null}
          onClick={() => {
            flow.setMode(flow.updateOverwrites ? 'overwrite' : 'update')
          }}
        >
          {flow.again === 'update' ? t('class.save.updateAgain') : t('class.save.update')}
        </Button>
      ) : null}
    </div>
  )
}

/** What MOWL holds for this class, one line under the title. */
export function SaveStatus({ flow }: { flow: SaveFlow }) {
  return (
    <p data-testid="saved-state" className="m-0 text-[0.9375rem] text-muted">
      {flow.stateLine}
    </p>
  )
}

export function SaveNotices({ flow }: { flow: SaveFlow }) {
  const t = useT()
  const { mode, action, summary, number } = flow
  const confirmRef = useRef<HTMLDivElement>(null)
  // The confirmation takes focus when it opens, so a keyboard user lands on its buttons.
  useEffect(() => {
    if (mode !== null) confirmRef.current?.focus()
  }, [mode])
  const close = () => {
    flow.setMode(null)
  }
  return (
    <>
      {flow.hasSaved && flow.detached ? <p className="muted m-0">{t('class.save.detachedOther')}</p> : null}
      {flow.current === null ? <p className="muted m-0">{t('class.save.noVersion')}</p> : null}
      {flow.decision ? <Notice kind="info">{flow.decision}</Notice> : null}
      {flow.finished ? (
        <Outcome
          done={flow.finished}
          soundless={summary.soundless}
          onUpdateAnyway={() => {
            flow.setMode('overwrite')
          }}
        />
      ) : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      {flow.lost ? <Notice>{t('class.save.lost')}</Notice> : null}
      {flow.run ? (
        <WriteRun
          key={flow.run.id}
          jobId={flow.run.id}
          kind={flow.run.kind}
          onLost={flow.onRunLost}
          onFinished={flow.onRunFinished}
        />
      ) : null}
      {flow.busy && !flow.writing ? <p className="muted m-0">{t('class.waitRunning')}</p> : null}
      {mode !== null ? (
        <div className="notice" role="group" aria-label={t('class.confirm')} tabIndex={-1} ref={confirmRef}>
          {mode === 'save' ? (
            <>
              <p>
                {t(summary.soundless ? 'class.save.confirmSaveSoundless' : 'class.save.confirmSave', {
                  number: String(number ?? '?'),
                })}
                {flow.hasSaved ? ` ${t('class.save.confirmSaveMore')}` : ''}
              </p>
              <ConfirmButtons
                label={t('class.save.title')}
                busy={action.busy}
                onConfirm={() => {
                  flow.start('save', false)
                }}
                onCancel={close}
              />
            </>
          ) : null}
          {mode === 'update' ? (
            <>
              <p>
                {t(summary.soundless ? 'class.save.confirmUpdateSoundless' : 'class.save.confirmUpdate', {
                  number: String(number ?? '?'),
                })}
              </p>
              <ConfirmButtons
                label={t('class.save.updateButton')}
                busy={action.busy}
                onConfirm={() => {
                  flow.start('update', false)
                }}
                onCancel={close}
              />
            </>
          ) : null}
          {mode === 'overwrite' ? (
            <>
              <p>
                <strong>{t('class.save.overwriteTitle')}</strong>{' '}
                {t('class.save.overwriteText', { number: String(number ?? '?') })}
              </p>
              <ConfirmButtons
                label={t('class.save.replace')}
                busy={action.busy}
                onConfirm={() => {
                  flow.start('update', true)
                }}
                onCancel={close}
              />
            </>
          ) : null}
        </div>
      ) : null}
      {flow.hasSaved && !flow.upToDate && flow.savedId === null ? (
        <p className="muted m-0" data-testid="update-unavailable">
          {t('class.save.updateUnavailable')}
        </p>
      ) : null}
    </>
  )
}
