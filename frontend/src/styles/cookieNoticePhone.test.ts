import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { PHONE_MEDIA } from '../components/imagery/imageSlots'

// Issue 1273: on a phone the cookie notice is a bar that takes room (the Landing hero, the end of the
// page and the other fixed bottom bars leave it free), not a card floating over the page, which could
// cover the hero's sign-up button. The browser check is e2e/tests/cookie-notice-phone.spec.ts; this
// test keeps the shape of the rules so a later edit cannot quietly bring the floating card back.
const dir = import.meta.dirname
const css = readFileSync(join(dir, 'app.css'), 'utf8')
const phone = PHONE_MEDIA.replace(/^\(|\)$/g, '')
const start = css.indexOf(`@media (${phone}) {\n  :root {`)

/** The phone block that holds the notice: from its `@media` to the closing brace at column 0. */
function phoneBlock(): string {
  expect(start, 'the notice phone block follows the shared phone breakpoint').toBeGreaterThanOrEqual(0)
  return css.slice(start, css.indexOf('\n}\n', start))
}

function rule(selector: string): string {
  const block = phoneBlock()
  const open = block.indexOf(`  ${selector} {`)
  expect(open, selector).toBeGreaterThanOrEqual(0)
  return block.slice(open, block.indexOf('  }', open))
}

describe('the cookie notice on a phone', () => {
  it('uses the same breakpoint as the Landing hero, so the two never disagree', () => {
    expect(PHONE_MEDIA).toBe('(max-width: 40rem)')
    expect(css).not.toContain('639px')
  })

  it('is a full-width bar at the foot of the page, above the tab bar when there is one', () => {
    const bar = rule('.cookie-notice')
    expect(bar).toMatch(/left:\s*0/)
    expect(bar).toMatch(/right:\s*0/)
    expect(bar).toContain('bottom: var(--cb-tabbar-h, 0px)')
    expect(bar).toMatch(/z-index:\s*25/) // under the tab bar (z-30), so the raised + is never covered
  })

  it('turns its measured height into --cb-notice-h, which is 0 everywhere else', () => {
    expect(css).toMatch(/:root\s*\{\s*--cb-notice-h:\s*0px;/)
    expect(rule(':root')).toContain('--cb-notice-h: var(--cb-notice-measured, 0px)')
  })

  it('keeps the end of every page clear of the bar', () => {
    expect(rule('body')).toContain('padding-bottom: var(--cb-notice-h)')
  })

  it('keeps the card in the corner on a wide screen', () => {
    const wide = css.slice(css.indexOf('.cookie-notice {'), start)
    expect(wide).toContain('position: fixed')
    expect(wide).toContain('left: var(--cb-space-4)')
    expect(wide).toContain('bottom: var(--cb-space-4)')
  })
})
