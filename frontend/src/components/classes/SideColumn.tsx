import { useState } from 'react'
import type { ClassMessage, ClassSummary, RevisionSummary } from '../../api/classes'
import { useT } from '../../i18n/I18n'
import { ChangeSongs } from '../songs/ChangeSongs'
import { ChatPanel } from './ChatPanel'
import { HistoryPanel } from './HistoryPanel'

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
  // Whether the class's history says Change it works (true until it says otherwise).
  const t = useT()
  const [canChange, setCanChange] = useState(true)
  return (
    <aside aria-label={t('class.side.label')} className="flex min-w-0 flex-col gap-5">
      <div>
        <ChangeSongs summary={summary} />
      </div>
      <ChatPanel
        summary={summary}
        revisions={revisions}
        messages={messages}
        canChange={canChange}
        onReload={reload}
        onView={onView}
      />
      <div id="versions" className="scroll-mt-4">
        <HistoryPanel
          classId={summary.id}
          revisions={revisions}
          viewingId={viewingId}
          onView={onView}
          onUse={onUse}
          onCanChange={setCanChange}
          busy={busy}
        />
      </div>
    </aside>
  )
}
