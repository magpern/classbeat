import { describe, expect, it } from 'vitest'
import { reauthReturnTo } from './prompts'

describe('reauthReturnTo', () => {
  it('adds the outcome marker and keeps the other query and the fragment (the Account tab)', () => {
    expect(reauthReturnTo('/account', '', '#data')).toBe('/account?oauth=reauthed#data')
    expect(reauthReturnTo('/classes/1', '?a=b', '#x')).toBe('/classes/1?a=b&oauth=reauthed#x')
  })

  it('works without a fragment', () => {
    expect(reauthReturnTo('/account', '')).toBe('/account?oauth=reauthed')
  })
})
