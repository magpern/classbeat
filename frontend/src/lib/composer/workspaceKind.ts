import type { ClassSummary } from '../../api/classes'

/** The revision origin of a sketch (ADR 0038 section 4). */
export const SKETCH = 'sketch'

/**
 * Which class page a class gets (ADR 0038 section 10.2 and its amendment of 2026-11-24). Pure. The
 * workspace is for a class the server says was made on the composer (`composer` in the class
 * answer, #1188): a ride reopens in the page it was made in, whatever the flag says today. When the
 * operator has turned the composer off, the workspace shows a paused notice instead of sending the
 * instructor to the older page. A soundless class ("No music": no sketch, its first design is the
 * class) keeps the page it has, as does every older class (the answer says `composer: false`) and a
 * running or studio class.
 */
export function isComposerClass(summary: Pick<ClassSummary, 'soundless' | 'composer'>): boolean {
  return summary.composer.composer && !summary.soundless
}
