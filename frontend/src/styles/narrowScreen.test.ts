import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// jsdom has no layout, so the guard for "no horizontal scroll at 320 px" on form panels is the
// rule itself (checked in a browser at 320 px: /account, Plan and Sign-in and security tabs).
// A grid with the implicit `auto` column takes the width of its widest input (about 266 px) as
// its minimum, so the panel grows past a narrow screen; `minmax(0, 1fr)` lets it shrink.
const formsCss = readFileSync(join(import.meta.dirname, 'forms.css'), 'utf8')

function rule(selector: string): string {
  const start = formsCss.indexOf(`\n${selector} {`)
  expect(start, selector).toBeGreaterThanOrEqual(0)
  return formsCss.slice(start, formsCss.indexOf('}', start))
}

describe('form panels on a narrow screen', () => {
  it.each(['.panel', '.field'])('%s is a grid whose column can shrink below its content', (selector) => {
    const block = rule(selector)
    expect(block).toContain('display: grid')
    expect(block).toMatch(/grid-template-columns:\s*minmax\(0,\s*1fr\)/)
  })
})
