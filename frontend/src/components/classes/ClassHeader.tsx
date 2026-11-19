import { Ellipsis } from 'lucide-react'
import { lazy, Suspense } from 'react'
import type { ClassSummary } from '../../api/classes'
import { classStatus } from '../../lib/classCards'
import { useT } from '../../i18n/I18n'
import { Button } from '../ui/button'
import { Chip } from '../ui/chip'
import { PageHeading } from '../ui/page-heading'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '../ui/dropdown-menu'
import { DuplicateClass } from './DuplicateClass'
import { SaveActions, SaveNotices, SaveStatus } from './SavePanel'
import { SendToPlayerActions } from './SendToPlayer'
import type { SaveFlow } from './useSaveFlow'
import type { PlayerFlow } from './useSendToPlayer'

// The confirmation, progress and outcome of a send load when a send has something to show.
const SendToPlayerNotices = lazy(() => import('./SendToPlayerNotices'))
// What the player holds of the class, for a user who has the player.
const PlayerSentState = lazy(() => import('./SendToPlayerNotices').then((m) => ({ default: m.PlayerSentState })))

/**
 * The header of the class page (ADR 0028 section 5: the primary action is not buried). The title,
 * the state chips and what MOWL holds are on the left; Save to MOWL, Send to player and the other actions are on the
 * right (they wrap under the title on a phone, the primary one first). The confirmation, progress and
 * outcome of a save show under the row, still inside the header.
 */
export function ClassHeader({
  summary,
  flow,
  player,
  viewingId,
  versionNumber,
  versionLine = null,
  canChangeSongs,
  deleting,
  onGoTo,
  onDelete,
}: {
  summary: ClassSummary
  flow: SaveFlow
  player: PlayerFlow
  viewingId: string | null
  versionNumber: number | null
  /** The shown version's "Version 1, first design" line, the heading's intro. */
  versionLine?: string | null
  canChangeSongs: boolean
  deleting: boolean
  onGoTo: (id: string) => void
  onDelete: () => void
}) {
  const t = useT()
  const status = classStatus(summary)
  return (
    <header data-testid="class-header" className="flex flex-col gap-3">
      <PageHeading
        eyebrow={t('class.page.eyebrow')}
        title={summary.title}
        titleClassName="break-words"
        intro={versionLine}
        actions={
        <div className="flex flex-wrap items-center gap-2">
          <SaveActions flow={flow} />
          <SendToPlayerActions flow={player} />
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
        }
      />
      <div className="flex flex-wrap items-center gap-2">
        <Chip tone={status.tone}>{status.label}</Chip>
        {summary.sport === 'running' ? <Chip data-testid="sport">{t('class.page.running')}</Chip> : null}
        {summary.soundless ? <Chip data-testid="soundless">{t('class.page.noMusic')}</Chip> : null}
      </div>
      <SaveStatus flow={flow} />
      {player.showsState ? (
        <Suspense fallback={null}>
          <PlayerSentState summary={summary} />
        </Suspense>
      ) : null}
      <SaveNotices flow={flow} />
      {player.active ? (
        <Suspense fallback={null}>
          <SendToPlayerNotices flow={player} />
        </Suspense>
      ) : null}
    </header>
  )
}
