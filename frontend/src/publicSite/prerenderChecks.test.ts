import { describe, expect, it } from 'vitest'
import { metaKeys } from './pageMeta'
import { checkHeading, checkNoInlineStyle, checkTranslated, neutralBody } from './prerenderChecks'
import { DRAFT_PATHS, LISTED_PATHS, PUBLISHED_PATHS } from './publicPaths'

describe('exactly one h1', () => {
  it.each([
    ['none', '<main><h2>x</h2></main>', 1],
    ['two', '<h1>a</h1><h1 id="b">b</h1>', 1],
    ['one', '<main><h1 id="a">a</h1></main>', 0],
    ['one with a header tag around', '<header><h1>a</h1></header><h10>no</h10>', 0],
  ])('%s', (_name, body, problems) => {
    expect(checkHeading(body)).toHaveLength(problems)
  })
})

describe('no inline style', () => {
  it('fails on a style attribute on any element', () => {
    expect(checkNoInlineStyle('<rect style="fill:red"/>')).toHaveLength(1)
    expect(checkNoInlineStyle('<div class="a" style="width:3px">x</div>')).toHaveLength(1)
    expect(checkNoInlineStyle('<div class="a"\n STYLE = "x">x</div>')).toHaveLength(1)
  })

  it('passes on classes, SVG attributes and a word in text', () => {
    expect(checkNoInlineStyle('<rect class="zone-3" fill="red"/><p>a style="x" sample</p>')).toEqual([])
  })
})

describe('translated bodies', () => {
  const english = '<a href="/en/signup">Join</a>'

  it('treats the language prefix of a link as no translation', () => {
    expect(neutralBody('<a href="/sv/signup">Join</a>', 'sv')).toBe(english)
    expect(neutralBody('<a href="/sv">x</a><a href="/svg">y</a>', 'sv')).toBe('<a href="/en">x</a><a href="/svg">y</a>')
    expect(checkTranslated('sv', '<a href="/sv/signup">Join</a>', english)).toHaveLength(1)
    expect(checkTranslated('da', '<a href="/da/signup">Join</a>', english)).toHaveLength(1)
  })

  it('passes once the text differs, and never judges English itself', () => {
    expect(checkTranslated('sv', '<a href="/sv/signup">Prova</a>', english)).toEqual([])
    expect(checkTranslated('en', english, english)).toEqual([])
  })
})

describe('the public paths', () => {
  it('publish the three legal-and-landing pages and keep Help a draft until PP8', () => {
    expect([...PUBLISHED_PATHS]).toEqual(['', '/privacy', '/terms'])
    expect([...DRAFT_PATHS]).toEqual(['/help', '/how-it-works', '/features'])
  })

  it('each have a title and a description key', () => {
    for (const path of LISTED_PATHS) expect(metaKeys(path).title).toMatch(/^meta\./)
    expect(() => metaKeys('/nowhere')).toThrow(/no title and description keys/)
  })
})
