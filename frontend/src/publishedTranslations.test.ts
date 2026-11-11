import { describe, expect, it } from 'vitest'
import { checkPublishedTranslations } from '../scripts/published-translations.ts'

// The on-demand check of PP8 (#897): a published page may not render a key on untranslated.json.

const page = (...keys: string[]) => ({ keys })
const PUBLISHED = ['', '/help']

describe('checkPublishedTranslations', () => {
  it('reports nothing when no recorded key is untranslated', () => {
    const prerendered = {
      en: { '': page('a'), '/help': page('b') },
      sv: { '': page('a'), '/help': page('b') },
      da: { '': page('a'), '/help': page('b') },
    }
    expect(checkPublishedTranslations(prerendered, PUBLISHED, ['elsewhere'])).toEqual({ problems: [], checked: 4 })
  })

  it('names the page and key of an untranslated key a published page renders', () => {
    const prerendered = { en: { '': page('a') }, sv: { '': page('a', 'late'), '/help': page('b') } }
    expect(checkPublishedTranslations(prerendered, PUBLISHED, ['late']).problems).toEqual([
      'sv/: late is on untranslated.json',
    ])
  })

  it('ignores a draft page and a key only the English page renders', () => {
    const prerendered = { en: { '': page('late') }, sv: { '': page('a'), '/help': page('b'), '/draft': page('late') } }
    expect(checkPublishedTranslations(prerendered, PUBLISHED, ['late']).problems).toEqual([])
  })

  it('fails a published page the prerender did not record, or a file without sv and da', () => {
    expect(checkPublishedTranslations({ en: {}, sv: { '': page('a') } }, PUBLISHED, []).problems).toEqual([
      'sv/help: not in prerender.json',
    ])
    expect(checkPublishedTranslations({ en: {} }, PUBLISHED, []).problems).toEqual([
      'prerender.json holds no language besides English',
    ])
  })
})
