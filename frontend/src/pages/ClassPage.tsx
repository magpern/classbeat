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
 * The class page. With `composer_flow` on, a composer class (ADR 0038 section 10.2) gets the
 * workspace; every other class, and every class with the flag off, gets the page it had. The flag
 * is read before either body mounts, so the two never both run their hand-off.
 */
function ClassView({ classId }: { classId: string }) {
  const t = useT()
  const { state, reload } = useClassData(classId)
  const flag = useComposerFlag()
  const [deleted, setDeleted] = useState<Deleted | null>(null)
  if (deleted !== null) {
    return (
      <Suspense fallback={null}>
        <DeletedClass deleted={deleted} />
      </Suspense>
    )
  }
  if (state.status !== 'ready') return <ClassUnavailable classId={classId} state={state} onRetry={reload} />
  if (flag === null) return <p className="m-0">{t('common.loading')}</p>
  const { summary, revisions } = state.data
  const Body = isComposerClass(flag, summary, revisions) ? ComposerWorkspace : ClassBody
  return <Body classId={classId} data={state.data} reload={reload} onDeleted={setDeleted} />
}
