import type { ClassMessage, ClassSummary, RevisionSummary } from '../../api/classes'
import { ChangeSongs } from '../songs/ChangeSongs'
import { ChatPanel } from './ChatPanel'
import { RevisionsPanel } from './RevisionsPanel'

/**
 * The side column of the class page (ADR 0013 section 5): Change songs, the Change it chat and the
 * versions. Save to MOWL and Delete are in the page header. Every panel keeps its own requests.
 */
export function SideColumn({
  summary,
  revisions,
  messages,
  viewingId,
  busy,
  onView,
  onUse,
  reload,
}: {
  summary: ClassSummary
  revisions: RevisionSummary[]
  messages: ClassMessage[]
  viewingId: string | null
  busy: boolean
  onView: (id: string | null) => void
  onUse: (revision: RevisionSummary) => void
  reload: () => void
}) {
  return (
    <aside aria-label="Save, change and versions" className="flex min-w-0 flex-col gap-5">
      <div>
        <ChangeSongs summary={summary} />
      </div>
      <ChatPanel summary={summary} revisions={revisions} messages={messages} onReload={reload} onView={onView} />
      <div id="versions" className="scroll-mt-4">
        <RevisionsPanel revisions={revisions} viewingId={viewingId} onView={onView} onUse={onUse} busy={busy} />
      </div>
    </aside>
  )
}
