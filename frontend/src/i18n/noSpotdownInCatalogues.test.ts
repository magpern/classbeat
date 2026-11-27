import { describe, expect, it } from 'vitest'
import da from './da'
import { en } from './en'
import sv from './sv'

// #1571 (owner, 2026-11-27): "for the user classbeat is all they know. Spotdown is just a service for
// classbeat." No string a user sees names spotdown; code, logs, docs and ADRs keep the name. `en`, `sv`
// and `da` hold every catalogue module (workspace, preview, editor, legal, help, public and the rest), so
// a new module spread into them is covered too. Plural forms are objects; every text in them is read.
const catalogues = { en, sv, da } as const

function texts(value: unknown, key: string): [string, string][] {
  if (typeof value === 'string') return [[key, value]]
  if (value !== null && typeof value === 'object')
    return Object.entries(value).flatMap(([form, inner]) => texts(inner, `${key}.${form}`))
  return []
}

function namingSpotdown(catalogue: Record<string, unknown>): string[] {
  return Object.entries(catalogue)
    .flatMap(([key, value]) => texts(value, key))
    .filter(([, text]) => /spotdown/i.test(text))
    .map(([key]) => key)
}

describe('the user-facing catalogues', () => {
  for (const lang of ['en', 'sv', 'da'] as const) {
    it(`${lang}: no string names spotdown`, () => {
      const catalogue = catalogues[lang] as Record<string, unknown>
      expect(Object.keys(catalogue).length).toBeGreaterThan(1000)
      expect(namingSpotdown(catalogue)).toEqual([])
    })
  }

  it('the check finds the name in any case, in a plain string and in a plural form', () => {
    expect(namingSpotdown({ a: 'Analysed by Spotdown.', b: { one: 'x', other: 'SPOTDOWN {count}' }, c: 'classbeat' })).toEqual(['a', 'b.other'])
  })
})
