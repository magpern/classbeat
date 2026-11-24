import { lazy, Suspense, useState } from 'react'
import type { Deleted } from '../api/classes'
import { ClassBody } from '../components/classes/ClassBody'
import { ClassUnavailable } from '../components/classes/ClassUnavailable'
import { ComposerWorkspace } from '../components/composer/ComposerWorkspace'
import { RequireAuth } from '../components/RequireAuth'
import { useComposerFlag } from '../hooks/composer/useComposerFlag'
import { useClassData } from '../hooks/useClassData'
import { useT } from '../i18n/I18n'
import { isComposerClass } from '../lib/composer/workspaceKind'

// The page after a deletion loads when a class is deleted.
const DeletedClass = lazy(() => import('../components/classes/DeletedClass'))

export function ClassPage({ classId }: { classId: string }) {
  return (
    <RequireAuth>
      <ClassView classId={classId} />
    </RequireAuth>
  )
}

/**
 * The class page. A class the server says is a composer class (ADR 0038
 * section 10.2) gets the workspace, with the flag on or off: it follows the class, not today's
 * flag. The flag is read only for a composer class, to show the paused notice; every other class
 * gets the page it had and never waits for it.
 */
function ClassView({ classId }: { classId: string }) {
  const t = useT()
  const { state, reload } = useClassData(classId)
  const summary = state.status === 'ready' ? state.data.summary : null
  const composer = summary !== null && summary.composer.composer && !summary.soundless
  const flag = useComposerFlag(composer)
  const [deleted, setDeleted] = useState<Deleted | null>(null)
  if (deleted !== null) {
    return (
      <Suspense fallback={null}>
        <DeletedClass deleted={deleted} />
      </Suspense>
    )
  }
  if (state.status !== 'ready') return <ClassUnavailable classId={classId} state={state} onRetry={reload} />
  if (isComposerClass(state.data.summary)) {
    if (flag === null) return <p className="m-0">{t('common.loading')}</p>
    return <ComposerWorkspace classId={classId} data={state.data} reload={reload} onDeleted={setDeleted} paused={flag === false} />
  }
  return <ClassBody classId={classId} data={state.data} reload={reload} onDeleted={setDeleted} />
}
