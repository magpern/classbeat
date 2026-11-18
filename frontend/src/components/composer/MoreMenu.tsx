import { Ellipsis } from 'lucide-react'
import { lazy, Suspense } from 'react'
import type { ClassSummary, RevisionSummary } from '../../api/classes'
import { PLAYER_PATH } from '../../api/player'
import { useUseRevision } from '../../hooks/useUseRevision'
import { useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'
import { BeatOnePanel } from '../classes/BeatOnePanel'
import { HistoryPanel } from '../classes/HistoryPanel'
import type { PlayerFlow } from '../classes/useSendToPlayer'
import { SwedebeatSection } from '../swedebeat/SwedebeatSection'
import { Button } from '../ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '../ui/dropdown-menu'

// The player's confirmation, progress and outcome load only when a send has something to show.
const SendToPlayerNotices = lazy(() => import('../classes/SendToPlayerNotices'))

export type MoreSection = 'beatOne' | 'swedebeat' | 'versions'

/**
 * The "More" menu (ADR 0038 section 10.2): Beat One, Send to player, the SwedeBeat section,
 * versions and delete. The other panels are not in the main view; a chosen one opens below the
 * class (see `MorePanel`) and nothing of a closed one is rendered.
 */
export function MoreMenu({
  summary,
  player,
  current,
  deleting,
  onPick,
  onDelete,
}: {
  summary: ClassSummary
  player: PlayerFlow
  /** The class page shows the current version, which Beat One needs. */
  current: boolean
  deleting: boolean
  onPick: (section: MoreSection) => void
  onDelete: () => void
}) {
  const t = useT()
  const status = player.player?.status
  const playerLink = status === 'not_connected' || status === 'needs_reconnect'
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="secondary" aria-label={t('cw.more')}>
          <Ellipsis aria-hidden="true" size={18} />
          {t('cw.more')}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        // Delete class moves focus to its confirmation card; the menu must not take it back.
        onCloseAutoFocus={(event) => {
          if (deleting) event.preventDefault()
        }}
      >
        {summary.beatOne.available && current ? (
          <DropdownMenuItem
            onSelect={() => {
              onPick('beatOne')
            }}
          >
            {t('cw.more.beatOne')}
          </DropdownMenuItem>
        ) : null}
        {player.offered && playerLink ? (
          <DropdownMenuItem asChild>
            <Link to={PLAYER_PATH}>{t('cw.more.player')}</Link>
          </DropdownMenuItem>
        ) : null}
        {player.offered && !playerLink ? (
          <DropdownMenuItem
            disabled={!player.canSend}
            onSelect={() => {
              player.setConfirming(true)
            }}
          >
            {t('cw.more.player')}
          </DropdownMenuItem>
        ) : null}
        {summary.sport === 'running' || !summary.swedebeatOn ? null : (
          <DropdownMenuItem
            onSelect={() => {
              onPick('swedebeat')
            }}
          >
            {t('cw.more.swedebeat')}
          </DropdownMenuItem>
        )}
        <DropdownMenuItem
          onSelect={() => {
            onPick('versions')
          }}
        >
          {t('cw.more.versions')}
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={onDelete}>{t('cw.more.delete')}</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** The chosen section of the More menu, below the class, with a Close; nothing when none is chosen. */
export function MorePanel({
  section,
  summary,
  revisions,
  viewingId,
  busy,
  player,
  onClose,
  onReload,
  onView,
  onCanChange,
}: {
  section: MoreSection | null
  summary: ClassSummary
  revisions: RevisionSummary[]
  viewingId: string | null
  busy: boolean
  player: PlayerFlow
  onClose: () => void
  onReload: () => void
  onView: (revisionId: string) => void
  onCanChange: (can: boolean) => void
}) {
  const t = useT()
  const using = useUseRevision(summary.id, onReload, onClose)
  return (
    <>
      {player.active ? (
        <Suspense fallback={null}>
          <SendToPlayerNotices flow={player} />
        </Suspense>
      ) : null}
      {section === null ? null : (
        <section data-testid="more-panel" className="flex flex-col gap-3 rounded-lg border border-line bg-surface p-4">
          {section === 'beatOne' ? (
            <BeatOnePanel key={summary.beatOne.revisionId ?? 'none'} summary={summary} busy={busy} onReload={onReload} />
          ) : null}
          {section === 'swedebeat' ? <SwedebeatSection summary={summary} onReload={onReload} /> : null}
          {section === 'versions' ? (
            <div id="versions">
              <HistoryPanel
                classId={summary.id}
                revisions={revisions}
                viewingId={viewingId}
                onView={onView}
                onUse={using.use}
                onCanChange={onCanChange}
                busy={using.busy || busy}
              />
            </div>
          ) : null}
          <div>
            <Button variant="secondary" onClick={onClose}>
              {t('cw.more.close')}
            </Button>
          </div>
        </section>
      )}
    </>
  )
}
