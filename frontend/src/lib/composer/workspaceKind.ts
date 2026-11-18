import type { ClassSummary, RevisionSummary } from '../../api/classes'

/** The revision origin of a sketch (ADR 0038 section 4). */
export const SKETCH = 'sketch'

/**
 * Which class page a class gets (ADR 0038 section 10.2). Pure. The workspace is for a composer
 * class: the flag is on, and the class has a sketch among its versions (it stays in the history
 * after phase 2), or it has no version yet and could still get one from the composer's hand-off.
 * A soundless class has no sketch (its first design is the class), a running class is not the
 * composer's, and a studio class has its own page; all keep the page they have.
 *
 * Assumption for the PR: the server does not say "made on the composer", so the sketch in the
 * history is the mark. A class with no version yet that was made on the Advanced form shows the
 * workspace while its first design runs, and the old page once that design arrives.
 */
export function isComposerClass(
  flag: boolean,
  summary: Pick<ClassSummary, 'sport' | 'soundless' | 'currentRevisionId'>,
  revisions: readonly Pick<RevisionSummary, 'origin'>[],
): boolean {
  if (!flag) return false
  if (revisions.some((r) => r.origin === SKETCH)) return true
  return summary.currentRevisionId === null && revisions.length === 0 && summary.sport === 'cycling' && !summary.soundless
}
