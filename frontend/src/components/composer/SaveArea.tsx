import type { WorkspaceState } from '../../lib/composer/chip'
import { useT } from '../../i18n/I18n'
import { Button } from '../ui/button'
import { SaveNotices } from '../classes/SavePanel'
import type { SaveFlow } from '../classes/useSaveFlow'

/**
 * What stands where the approve bar was once the class is a draft (ADR 0038 sections 7 and 10.2):
 * nothing while `save_pending` is set (the chip says "Saving…" and no Save button shows, so the
 * driver and a manual Save never both save), the ordinary **Save** after a failed save or a fit that
 * did not keep the preview, and **Update workout** once the saved class has changed. When the server
 * says the final songs differed (`phase2.state` is `differed`: the draft was re-designed and is not
 * saved by itself) the note asks the instructor to check the class; it goes once that draft is saved
 * and up to date. The confirmation, progress and outcome are the class page's own (`SaveNotices`).
 */
export function SaveArea({
  flow,
  chip,
  currentOrigin,
  differed,
}: {
  flow: SaveFlow
  chip: WorkspaceState
  currentOrigin: string | null
  /** The class answer's `phase2.state === 'differed'`. */
  differed: boolean
}) {
  const t = useT()
  const isDraft = currentOrigin !== null && currentOrigin !== 'sketch'
  const showSave = isDraft && chip !== 'saving' && flow.mode === null && !flow.hasSaved
  const showUpdate = isDraft && chip !== 'saving' && flow.mode === null && flow.showUpdate
  const showDiffered = differed && isDraft && chip !== 'saving' && !(flow.hasSaved && flow.upToDate)
  return (
    <section aria-label={t('cw.save.region')} data-testid="save-area" className="flex flex-col gap-3">
      {showDiffered ? <p className="m-0 text-[0.9375rem] text-muted">{t('cw.approve.differed')}</p> : null}
      {showSave || showUpdate ? (
        <div className="flex flex-wrap gap-2">
          {showSave ? (
            <Button
              disabled={!flow.canWrite}
              onClick={() => {
                flow.setMode('save')
              }}
            >
              {t('class.save.title')}
            </Button>
          ) : null}
          {showUpdate ? (
            <Button
              disabled={!flow.canWrite || flow.savedId === null}
              onClick={() => {
                flow.setMode(flow.updateOverwrites ? 'overwrite' : 'update')
              }}
            >
              {flow.again === 'update' ? t('class.save.updateAgain') : t('class.save.update')}
            </Button>
          ) : null}
        </div>
      ) : null}
      <SaveNotices flow={flow} />
    </section>
  )
}
