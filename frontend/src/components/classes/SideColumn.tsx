import type { ClassMessage, ClassSummary, RevisionSummary } from '../../api/classes'
import { ChangeSongs } from '../songs/ChangeSongs'
import { ChatPanel } from './ChatPanel'
import { DeleteClass } from './DeleteClass'
import { RevisionsPanel } from './RevisionsPanel'
import { SavePanel } from './SavePanel'

/**
 * The side column of the class page (ADR 0013 section 5): Save to MOWL first, then Change songs,
 * the Change it chat, the versions and Delete. Every panel keeps its own requests unchanged.
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
  onDeleted,
}: {
  summary: ClassSummary
  revisions: RevisionSummary[]
  messages: ClassMessage[]
  viewingId: string | null
  busy: boolean
  onView: (id: string | null) => void
  onUse: (revision: RevisionSummary) => void
  reload: () => void
  onDeleted: (kept: number) => void
}) {
  return (
    <aside aria-label="Save, change and versions" className="flex min-w-0 flex-col gap-5">
      <SavePanel summary={summary} onReload={reload} />
      <ChangeSongs summary={summary} />
      <ChatPanel summary={summary} revisions={revisions} messages={messages} onReload={reload} onView={onView} />
      <RevisionsPanel revisions={revisions} viewingId={viewingId} onView={onView} onUse={onUse} busy={busy} />
      <DeleteClass summary={summary} onDeleted={onDeleted} onReload={reload} />
    </aside>
  )
}
