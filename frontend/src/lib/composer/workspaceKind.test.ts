import { describe, expect, it } from 'vitest'
import { isComposerClass } from './workspaceKind'

import { NO_COMPOSER_STATE } from '../../api/composerState'

const composerClass = { soundless: false, composer: { ...NO_COMPOSER_STATE, composer: true } }

describe('which class page a class gets', () => {
  const rows: [string, Parameters<typeof isComposerClass>[0], boolean][] = [
    ['the server says composer: the workspace, also when the flag is off today', composerClass, true],
    ['the server says not composer (an older class, an Advanced form class)', { ...composerClass, composer: NO_COMPOSER_STATE }, false],
    ['a soundless class has no sketch: the old page', { ...composerClass, soundless: true }, false],
    ['neither: the old page', { soundless: false, composer: NO_COMPOSER_STATE }, false],
  ]
  it.each(rows)('%s', (_name, summary, expected) => {
    expect(isComposerClass(summary)).toBe(expected)
  })
})
