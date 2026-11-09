import { Ellipsis } from 'lucide-react'
import type { ClassSummary } from '../../api/classes'
import { classStatus } from '../../lib/classCards'
import { useT } from '../../i18n/I18n'
import { Button } from '../ui/button'
import { Chip } from '../ui/chip'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '../ui/dropdown-menu'
import { DuplicateClass } from './DuplicateClass'
import { SaveActions, SaveNotices, SaveStatus } from './SavePanel'
import type { SaveFlow } from './useSaveFlow'

/**
 * The header of the class page (ADR 0028 section 5: the primary action is not buried). The title,
 * the state chips and what MOWL holds are on the left; Save to MOWL and the other actions are on the
 * right (they wrap under the title on a phone, the primary one first). The confirmation, progress and
 * outcome of a save show under the row, still inside the header.
 */
export function ClassHeader({
  summary,
  flow,
  viewingId,
  versionNumber,
  canChangeSongs,
  deleting,
  onGoTo,
  onDelete,
}: {
  summary: ClassSummary
  flow: SaveFlow
  viewingId: string | null
  versionNumber: number | null
  canChangeSongs: boolean
  deleting: boolean
  onGoTo: (id: string) => void
  onDelete: () => void
}) {
  const t = useT()
  const status = classStatus(summary)
  return (
    <header data-testid="class-header" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
        <div className="flex min-w-0 flex-1 basis-72 flex-col gap-2">
          <h1 className="m-0 font-display text-3xl font-extrabold break-words">{summary.title}</h1>
          <div className="flex flex-wrap items-center gap-2">
            <Chip tone={status.tone}>{status.label}</Chip>
            {summary.sport === 'running' ? <Chip data-testid="sport">{t('class.page.running')}</Chip> : null}
            {summary.soundless ? <Chip data-testid="soundless">{t('class.page.noMusic')}</Chip> : null}
          </div>
          <SaveStatus flow={flow} />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <SaveActions flow={flow} />
          {canChangeSongs ? (
            <Button
              variant="secondary"
              onClick={() => {
                onGoTo('change-songs')
              }}
            >
              {t('class.page.changeSongs')}
            </Button>
          ) : null}
          <DuplicateClass
            classId={summary.id}
            title={summary.title}
            revisionId={viewingId}
            versionNumber={versionNumber}
          />
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="secondary" aria-label={t('class.page.moreActions')}>
                <Ellipsis aria-hidden="true" size={18} />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              align="end"
              // Delete class moves focus to its confirmation card; the menu must not take it back.
              onCloseAutoFocus={(event) => {
                if (deleting) event.preventDefault()
              }}
            >
              <DropdownMenuItem
                onSelect={() => {
                  onGoTo('versions')
                }}
              >
                {t('class.versions')}
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={onDelete}>{t('class.page.deleteClass')}</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
      <SaveNotices flow={flow} />
    </header>
  )
}
