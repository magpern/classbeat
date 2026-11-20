import { describe, expect, it } from 'vitest'
import { getClass } from './classes'
import { CLASS_PATH, classBody } from '../test/classFixtures'
import { CLASS_ID } from '../test/jobFixtures'
import { fakeApi } from '../test/fakeApi'

describe('the class summary studio flag (#1173)', () => {
  it.each([
    [{ studio: true }, true],
    [{ studio: false }, false],
    [{}, false],
    [{ studio: 'true' }, false],
    [{ studio: null }, false],
  ])('reads %j as %s', async (extra, expected) => {
    fakeApi({ [`GET ${CLASS_PATH}`]: classBody(extra) })
    expect((await getClass(CLASS_ID)).studio).toBe(expected)
  })
})
