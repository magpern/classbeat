import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { PHONE_MEDIA } from '../components/imagery/imageSlots'

// Issue 1270: on a phone the Landing's first screen (badge, title, lead, button) is in view with no
// scroll. The rule that gives that is the hero's phone block in imagery.css: its height comes from the
// small viewport (`svh`, so the browser toolbar counts) minus a header allowance, with a `vh` fallback,
// and its text block is compact. The browser check is e2e/tests/landing-phone.spec.ts; this test keeps
// the rule's shape so a later edit cannot quietly drop it.
const dir = import.meta.dirname
const css = readFileSync(join(dir, 'imagery.css'), 'utf8')

const query = PHONE_MEDIA.replace(/^\(|\)$/g, '')
const start = css.indexOf(`@media (${query}) {\n  .app-main:has(.hero)`)

/** The phone block: from its `@media` to the closing brace at column 0. */
function phoneBlock(): string {
  expect(start, 'the hero phone block follows the shared phone breakpoint').toBeGreaterThanOrEqual(0)
  return css.slice(start, css.indexOf('\n}\n', start))
}

/** The declarations of one rule inside the phone block. */
function rule(selector: string): string {
  const block = phoneBlock()
  const open = block.indexOf(`  ${selector} {`)
  expect(open, selector).toBeGreaterThanOrEqual(0)
  return block.slice(open, block.indexOf('  }', open))
}

describe('the hero on a phone', () => {
  it('is sized from the small viewport, with the large one as the fallback declared first', () => {
    const hero = rule('.hero')
    const fallback = hero.indexOf('calc(100vh - var(--hero-chrome))')
    const small = hero.indexOf('calc(100svh - var(--hero-chrome))')
    expect(fallback).toBeGreaterThanOrEqual(0)
    expect(small).toBeGreaterThan(fallback) // a browser without svh drops the second and keeps the first
  })

  it('stays between 25rem and 32rem, so the text block always fits and the photo never grows past what it was', () => {
    for (const line of rule('.hero').split('\n').filter((l) => l.includes('min-height:'))) {
      expect(line).toMatch(/clamp\(25rem, calc\(100s?vh - var\(--hero-chrome\)\), 32rem\)/)
    }
  })

  it('allows for a header of three rows (the Swedish nav on 360 px) and the Landing padding', () => {
    const chrome = /--hero-chrome:\s*([\d.]+)rem/.exec(rule('.hero'))?.[1]
    // 165 px header + 16 px padding at 360 px in Swedish is 11.3rem
    expect(Number(chrome)).toBeGreaterThanOrEqual(11.3)
  })

  it('cuts the Landing top padding to a small step, for the Landing only', () => {
    expect(rule('.app-main:has(.hero)')).toMatch(/padding-top:\s*var\(--cb-space-[1-5]\)/)
  })

  it('keeps the text at the bottom of the hero, over the photo', () => {
    expect(rule('.hero')).toContain('justify-content: flex-end')
    expect(rule('.hero__text')).toMatch(/gap:\s*var\(--cb-space-[1-4]\)/)
  })

  it('shrinks the title and the lead and drops the badge margin that the gap replaces', () => {
    expect(rule('.hero__title')).toMatch(/font-size:\s*var\(--cb-text-3xl\)/)
    expect(rule('.hero__lead')).toMatch(/font-size:\s*var\(--cb-text-md\)/)
    expect(rule('.hero__text .badge')).toContain('margin-bottom: 0')
  })

  it('leaves the wide-screen rules alone', () => {
    const wide = css.slice(css.indexOf('.hero {'), start).replace(/\/\*[\s\S]*?\*\//g, '')
    expect(wide).toContain('min-height: 24rem;')
    expect(wide).toContain('max-width: min(34rem, 60%);')
    expect(wide).not.toContain('svh')
  })
})
