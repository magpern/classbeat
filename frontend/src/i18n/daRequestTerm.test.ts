import { describe, expect, it } from 'vitest'
import da from './da'

// #1562: one Danish term for the instructor's "request" ("anmodning", used in about 65 strings), not a
// second one ("forespørgsel") beside it. `da` includes the workspace and preview catalogues. The privacy
// page (`privacy.*`) says "forespørgsel" for a network request to a provider; it is legal text with an
// open owner question (docs/status.md), so it is left as it is and not checked here.
describe('the Danish catalogue', () => {
  it('says "anmodning" for a request, never "forespørgsel"', () => {
    const strings = Object.entries(da).filter((entry): entry is [string, string] => typeof entry[1] === 'string' && !entry[0].startsWith('privacy.'))
    expect(strings.length).toBeGreaterThan(1000)
    expect(strings.filter(([, text]) => /forespørg/i.test(text)).map(([key]) => key)).toEqual([])
    expect(strings.filter(([, text]) => /anmodning/i.test(text)).length).toBeGreaterThan(50)
  })
})
