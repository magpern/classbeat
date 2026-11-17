import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// jsdom applies no CSS, so a component test cannot see that `<div hidden class="flex">` is still on screen.
// Tailwind is loaded without its preflight (tailwind.css), so nothing turned `[hidden]` into `display: none`
// and a display utility (`flex`, `grid`, ...) beat the browser's own rule: the music list under the Music
// button was open before it was clicked (#1194, checked in Chromium). The guard is the rule itself.
const tailwindCss = readFileSync(join(import.meta.dirname, 'tailwind.css'), 'utf8')

function withoutComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, '')
}

interface HiddenRule {
  declarations: string
  /** True when the rule sits inside an `@layer name { ... }` block; false when it is unlayered. */
  layered: boolean
}

/** The `[hidden]` rule that sets `display`, or null. Looks one level deep: top level and directly inside `@layer`. */
function hiddenRule(css: string): HiddenRule | null {
  const source = withoutComments(css)
  const match = /(^|[{};])\s*(\[hidden\][^{}]*)\{([^{}]*)\}/g
  for (const found of source.matchAll(match)) {
    const declarations = found[3] ?? ''
    if (!/(^|;)\s*display\s*:/.test(declarations)) continue
    const before = source.slice(0, found.index + (found[1]?.length ?? 0))
    const open = (before.match(/\{/g) ?? []).length - (before.match(/\}/g) ?? []).length
    return { declarations, layered: open > 0 }
  }
  return null
}

/**
 * Layers are `theme, base, utilities`, so a normal declaration in `base` loses to every utility. The
 * rule wins over a display utility only when it is `!important` (an important declaration in any layer
 * beats a normal one, and an earlier layer beats a later one) or when it is unlayered.
 */
function beatsDisplayUtilities(rule: HiddenRule | null): boolean {
  if (rule === null) return false
  return /display\s*:\s*none\s*!important/.test(rule.declarations) || (!rule.layered && /display\s*:\s*none/.test(rule.declarations))
}

describe('the hidden attribute (tailwind.css)', () => {
  it('declares the layer order the rule below relies on: base before utilities', () => {
    const order = /@layer\s+([a-z, ]+);/.exec(withoutComments(tailwindCss))?.[1]
    const layers = (order ?? '').split(',').map((name) => name.trim())
    expect(layers.indexOf('base')).toBeGreaterThanOrEqual(0)
    expect(layers.indexOf('base')).toBeLessThan(layers.indexOf('utilities'))
  })

  it('has a [hidden] rule with display: none that a display utility cannot override', () => {
    const rule = hiddenRule(tailwindCss)
    expect(rule, 'tailwind.css has no [hidden] rule that sets display').not.toBeNull()
    expect(rule?.declarations).toMatch(/display\s*:\s*none/)
    expect(beatsDisplayUtilities(rule)).toBe(true)
  })

  it('keeps hidden="until-found" out of the rule, as the browser does', () => {
    expect(withoutComments(tailwindCss)).toMatch(/\[hidden\]:where\(:not\(\[hidden=['"]until-found['"]\]\)\)/)
  })

  describe('the checker', () => {
    it.each([
      ['an important rule in the base layer', '@layer base { [hidden] { display: none !important; } }', true],
      ['an unlayered rule', '[hidden] { display: none; }', true],
      ['a normal rule in the base layer, which every utility beats', '@layer base { [hidden] { display: none; } }', false],
      ['a normal rule in a layer', '@layer utilities { [hidden] { display: none; } }', false],
      ['no rule at all', '.flex { display: flex; }', false],
      ['a rule that does not set display', '[hidden] { color: red; }', false],
      ['the rule only in a comment', '/* [hidden] { display: none; } */ .flex { display: flex; }', false],
    ])('%s', (_name, css, expected) => {
      expect(beatsDisplayUtilities(hiddenRule(css))).toBe(expected)
    })
  })
})
