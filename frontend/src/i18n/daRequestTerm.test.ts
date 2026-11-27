import { describe, expect, it } from 'vitest'
import da from './da'

// #1562: one Danish term for the instructor's "request" ("anmodning", used in about 65 strings), not a
// second one ("forespørgsel") beside it. `da` includes the workspace and preview catalogues. The privacy
// page says "forespørgsel" for a network request to a provider in exactly three strings; it is legal text, so
// those keys are named here and nothing else is exempt (#1564): a later privacy string that says it for the instructor's request is caught.
const NETWORK_REQUEST_KEYS = ['privacy.processors.li2', 'privacy.processors.li5', 'privacy.processors.li6']

const stringsOf = () => Object.entries(da).filter((entry): entry is [string, string] => typeof entry[1] === 'string')
const withTerm = (entries: [string, string][]) => entries.filter(([, text]) => /forespørg/i.test(text)).map(([key]) => key)

describe('the Danish catalogue', () => {
  it('says "anmodning" for a request, never "forespørgsel"', () => {
    const strings = stringsOf().filter(([key]) => !NETWORK_REQUEST_KEYS.includes(key))
    expect(strings.length).toBeGreaterThan(1000)
    expect(withTerm(strings)).toEqual([])
    expect(strings.filter(([, text]) => /anmodning/i.test(text)).length).toBeGreaterThan(50)
  })

  it('keeps the exemption to the three privacy strings that use the term, no more and no fewer', () => {
    // sorted on both sides: the catalogue's own order is not part of the rule (#1569)
    expect(withTerm(stringsOf().filter(([key]) => key.startsWith('privacy.'))).sort()).toEqual(
      [...NETWORK_REQUEST_KEYS].sort(),
    )
  })
})
