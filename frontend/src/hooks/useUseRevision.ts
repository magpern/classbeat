import { currentReasonText, isStale } from '../api/classMessages'
import { getRevision, pickCurrent, type RevisionSummary } from '../api/classes'
import { useAction } from '../components/forms'
import { contentHash } from '../lib/canonical'

/** "Use this version": makes a version of the class the current one (ADR 0007 section 2). */
export function useUseRevision(classId: string, reload: () => void, onDone: () => void) {
  const pick = useAction()
  const use = (revision: RevisionSummary) => {
    void pick
      .run(
        async () => {
          // The hash of the content the server holds for this version: it only goes through if
          // the revision is the one named.
          const full = await getRevision(classId, revision.id)
          return pickCurrent(classId, revision.id, await contentHash(full.raw))
        },
        (error) => {
          if (isStale(error)) reload()
          return currentReasonText(error)
        },
      )
      .then((done) => {
        if (done !== undefined) {
          onDone()
          reload()
        }
      })
  }
  return { use, error: pick.error, upgrade: pick.upgrade, busy: pick.busy }
}
