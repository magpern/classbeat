import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// ADR 0013 section 2, amendment of 2026-11-11: the rules motion follows, checked on the sheets.
const SHEETS = ['motion.css', 'flywheel.css', 'motion-app.css', 'motion-feedback.css'] as const
const css = (name: string) => readFileSync(join(import.meta.dirname, name), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')

/** The sheet without its `@media <query> { … }` blocks (nested braces included). */
function without(sheet: string, query: string): string {
  const open = `@media (${query}) {`
  let rest = sheet
  for (let at = rest.indexOf(open); at >= 0; at = rest.indexOf(open)) {
    let depth = 1
    let end = at + open.length
    while (depth > 0 && end < rest.length) {
      if (rest[end] === '{') depth++
      else if (rest[end] === '}') depth--
      end++
    }
    rest = rest.slice(0, at) + rest.slice(end)
  }
  return rest
}

/** The body of `@keyframes name`. */
function keyframes(sheet: string, name: string): string | undefined {
  return new RegExp(`@keyframes ${name} \\{([\\s\\S]*?)\\n\\}`).exec(sheet)?.[1]
}

describe('reduced motion', () => {
  it.each(SHEETS)('%s starts an animation only under prefers-reduced-motion: no-preference', (name) => {
    // Everywhere else in the sheet, before or after that block, an animation may only be declared
    // paused (the flywheel's still frame), and no scroll timeline may be set.
    const calm = without(css(name), 'prefers-reduced-motion: no-preference')
    expect(calm.length).toBeLessThan(css(name).length)
    for (const rule of calm.match(/animation:[^;]+;/g) ?? []) expect(rule, name).toMatch(/\bpaused;$/)
    expect(calm).not.toMatch(/animation-(timeline|name|delay)/)
  })

  it('the checker sees a moving rule placed after the block', () => {
    const sheet = '@media (prefers-reduced-motion: no-preference) {\n  .a {\n    animation: x 1s;\n  }\n}\n.b {\n  animation: x 1s;\n}\n'
    expect(without(sheet, 'prefers-reduced-motion: no-preference')).toBe('\n.b {\n  animation: x 1s;\n}\n')
  })

  it('keeps the flywheel a still picture: its wave stays paused at its phase, at its own length', () => {
    // tailwind.css cuts every animation to 0.01 ms and one round under reduced motion. The paused
    // wave needs its real length and its endless rounds for a negative delay to be a phase.
    const sheet = css('flywheel.css')
    const wave = /animation: fw-wave ([\d.]+s) [^;]*\bpaused;/.exec(sheet)?.[1]
    expect(wave).toBeDefined()
    const reduced = /@media \(prefers-reduced-motion: reduce\) \{([\s\S]*?)\n\}/.exec(sheet)?.[1] ?? ''
    expect(reduced).toContain('.flywheel__bars rect')
    expect(reduced).toContain(`animation-duration: ${String(wave)} !important;`)
    expect(reduced).toContain('animation-iteration-count: infinite !important;')
  })
})

describe('what an entrance may change', () => {
  const all = SHEETS.map(css).join('\n')

  it('moves text and never fades or recolours it: only pictures change opacity', () => {
    // A block can rest half-way through its entrance (at the bottom edge of the screen, or in the
    // first frames of a list), so these keyframes must leave contrast alone. The others belong to
    // pictures: the playhead and veil, the dot, the flywheel, the burst.
    for (const name of ['reveal-in', 'card-arrive', 'badge-fill', 'row-in', 'notice-pop', 'check-pop', 'chart-in', 'draw-in']) {
      const body = keyframes(all, name)
      expect(body, name).toBeDefined()
      expect(body, name).not.toMatch(/opacity|(^|[^-])color:|filter/)
    }
  })

  it('clips only pictures, and never one that is fully on screen', () => {
    const sheet = css('motion.css')
    // On scroll, a clip belongs to an <svg> and ends when the picture has fully entered the screen.
    const rule = /([^{}]*)\{[^{}]*animation: draw-in[^{}]*\}/.exec(sheet)
    expect(rule?.[1]?.trim().split(/,\s*/)).toEqual(['svg.draw-in', '.draw-in-plot svg'])
    expect(rule?.[0]).toContain('animation-timeline: view();')
    expect(rule?.[0]).toContain('animation-range: entry 0% entry 100%;')
    // No other scroll-driven keyframes clip anything.
    for (const name of ['reveal-in', 'card-arrive', 'badge-fill', 'rail-fill']) expect(keyframes(sheet, name), name).not.toMatch(/clip-path/)
  })

  it('lights a card under its text, not over it', () => {
    const sheet = css('motion.css')
    expect(/\.glow-card \{[^}]*isolation: isolate;/.test(sheet)).toBe(true)
    expect(/\.glow-card::before \{[^}]*z-index: -1;/.test(sheet)).toBe(true)
  })
})

describe('scroll-driven animations', () => {
  it('are used only where the browser has them', () => {
    const sheet = css('motion.css')
    const before = sheet.slice(0, sheet.indexOf('@supports (animation-timeline: view())'))
    expect(before).not.toMatch(/animation-timeline/)
    expect(sheet).toMatch(/animation-timeline: view\(/)
  })
})

// The public pages are compared before and after the client takes over the prerendered HTML
// (e2e/tests/public-takeover-cells.ts): motion there must be CSS, not a state a script sets.
describe('the public pages', () => {
  it('set no reveal or motion state from script', () => {
    const source = (path: string) => readFileSync(join(import.meta.dirname, '..', path), 'utf8')
    for (const path of [
      'pages/LandingPage.tsx',
      'pages/HowItWorksPage.tsx',
      'pages/FeaturesPage.tsx',
      'pages/ExamplesPage.tsx',
      'pages/ExamplePage.tsx',
      'components/landing/LandingHero.tsx',
      'components/landing/HowItWorks.tsx',
      'components/landing/Flywheel.tsx',
      'components/classes/MiniProfile.tsx',
    ]) {
      expect(source(path), path).not.toMatch(/useEffect|useLayoutEffect|IntersectionObserver|setAttribute|classList/)
    }
  })
})
