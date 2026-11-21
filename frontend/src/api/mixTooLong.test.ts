import { describe, expect, it } from 'vitest'
import { failureText } from './jobMessages'

// ADR 0036 amendment B, B6 (#1371): a studio pick for a class no list of whole songs can fit fails
// `mix_too_long`, and the page says the class is too long, not the generic fallback.
describe('mix_too_long copy', () => {
  it('shows its own title and detail', () => {
    const found = failureText('mix_too_long')
    expect(found.title).toBe('This class is too long for a mix')
    expect(found).not.toEqual(failureText('some_unknown_category'))
  })
})
