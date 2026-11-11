import { describe, expect, it } from 'vitest'
import da from '../i18n/da'
import { en, type CatalogueKey, type Message } from '../i18n/en'
import untranslated from '../i18n/untranslated.json'
import sv from '../i18n/sv'
import { metaKeys } from './pageMeta'
import { LISTED_PATHS } from './publicPaths'

// The title and description of each public page (ADR 0028 section 2, ADR 0030 section 3). The server
// puts them in the head verbatim, so the length rules are checked here, on the catalogue keys.

const MAX_TITLE = 60
const MAX_WORDS = 25
const LANGUAGES: Record<string, Partial<Record<CatalogueKey, Message>>> = { en, sv, da }
// A page whose key is not translated yet shows the English text, as everywhere else.
function text(lang: string, key: CatalogueKey): string {
  const message = LANGUAGES[lang]?.[key] ?? en[key]
  if (typeof message !== 'string') throw new Error(`${key} must be plain text`)
  return message
}

describe.each(Object.keys(LANGUAGES))('the page copy in %s', (lang) => {
  it.each(LISTED_PATHS)('%j follows the length rules', (path) => {
    const keys = metaKeys(path)
    const title = text(lang, keys.title)
    const description = text(lang, keys.description)
    expect(title.length).toBeGreaterThan(0)
    expect(title.length).toBeLessThanOrEqual(MAX_TITLE)
    expect(title).toContain('classbeat') // the brand belongs in a title
    expect(description.split(/\s+/).length).toBeLessThanOrEqual(MAX_WORDS)
    expect(description.split(/\s+/)[0]).not.toBe('classbeat') // never the sentence's subject
    expect(`${title} ${description}`.toLowerCase()).not.toContain('spinning') // a trademark
  })

  it('the landing description uses the language\'s own word for indoor cycling', () => {
    // A description on untranslated.json shows the English text until PP7 (ADR 0023 section 8).
    if (lang !== 'en' && untranslated.includes(metaKeys('').description)) return
    const word = { en: 'indoor-cycling', sv: 'inomhuscykling', da: 'indendørscykling' }[lang]
    expect(text(lang, metaKeys('').description)).toContain(word)
  })
})
