import { describe, expect, it } from 'vitest'
import { isComposerClass } from './workspaceKind'

import { NO_COMPOSER_STATE } from '../../api/composerState'

const cycling = { sport: 'cycling' as const, soundless: false, currentRevisionId: 'r1', composer: NO_COMPOSER_STATE }
const sketch = [{ origin: 'sketch' }]
const designed = [{ origin: 'design_draft' }]

describe('which class page a class gets', () => {
  const rows: [string, boolean, Parameters<typeof isComposerClass>[1], { origin: string }[], boolean][] = [
    ['flag off: never', false, cycling, sketch, false],
    ['a sketch in the history', true, cycling, sketch, true],
    ['a draft after phase 2, the sketch earlier in the history', true, cycling, [{ origin: 'design_with_songs' }, ...sketch], true],
    ['an older class with no sketch', true, cycling, designed, false],
    ['no version yet, cycling', true, { ...cycling, currentRevisionId: null }, [], true],
    ['no version yet, soundless', true, { ...cycling, currentRevisionId: null, soundless: true }, [], false],
    ['the server says composer', true, { ...cycling, composer: { ...NO_COMPOSER_STATE, composer: true } }, designed, true],
    ['the server says not composer, whatever the history holds', true, { ...cycling, composer: { ...NO_COMPOSER_STATE, composer: false } }, sketch, false],
    ['the server says composer for a soundless class', true, { ...cycling, soundless: true, composer: { ...NO_COMPOSER_STATE, composer: true } }, designed, false],
    ['no version yet, running', true, { ...cycling, currentRevisionId: null, sport: 'running' }, [], false],
  ]
  it.each(rows)('%s', (_name, flag, summary, revisions, expected) => {
    expect(isComposerClass(flag, summary, revisions)).toBe(expected)
  })
})
