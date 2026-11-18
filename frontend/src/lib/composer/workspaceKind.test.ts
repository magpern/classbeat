import { describe, expect, it } from 'vitest'
import { isComposerClass } from './workspaceKind'

import { NO_COMPOSER_STATE } from '../../api/composerState'

const composerClass = { soundless: false, composer: { ...NO_COMPOSER_STATE, composer: true } }

describe('which class page a class gets', () => {
  const rows: [string, boolean, Parameters<typeof isComposerClass>[1], boolean][] = [
    ['the server says composer, the flag is on', true, composerClass, true],
    ['flag off: never', false, composerClass, false],
    ['the server says not composer (an older class, an Advanced form class)', true, { ...composerClass, composer: NO_COMPOSER_STATE }, false],
    ['a soundless class has no sketch: the old page', true, { ...composerClass, soundless: true }, false],
    ['neither: the old page', false, { soundless: false, composer: NO_COMPOSER_STATE }, false],
  ]
  it.each(rows)('%s', (_name, flag, summary, expected) => {
    expect(isComposerClass(flag, summary)).toBe(expected)
  })
})
