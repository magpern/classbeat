import type { ClassSummary } from '../../api/classes'

/** The revision origin of a sketch (ADR 0038 section 4). */
export const SKETCH = 'sketch'

/**
 * Which class page a class gets (ADR 0038 section 10.2). Pure. The workspace is for a class the
 * server says was made on the composer (`composer` in the class answer, #1188): it was read with
 * the flag on, so it has a sketch or will get one. The flag must be on too, and a soundless class
 * ("No music": no sketch, its first design is the class) keeps the page it has, as does every older
 * class (the answer says `composer: false`) and a running or studio class.
 */
export function isComposerClass(flag: boolean, summary: Pick<ClassSummary, 'soundless' | 'composer'>): boolean {
  return flag && summary.composer.composer && !summary.soundless
}
