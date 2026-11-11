import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { renderToString } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { Flywheel } from './Flywheel'

const css = (name: string) => readFileSync(join(import.meta.dirname, '../../styles', name), 'utf8')

describe('Flywheel', () => {
  const html = renderToString(<Flywheel />)

  it('is a decorative picture the prerender can write: no style attribute, hidden from assistive technology', () => {
    expect(html).not.toMatch(/\sstyle=/)
    expect(html).toMatch(/<svg[^>]*aria-hidden="true"/)
  })

  it('has 96 bars, each turned to its place round the ring', () => {
    const turns = [...html.matchAll(/<g transform="rotate\(([\d.]+) 150 150\)"><rect/g)].map((m) => Number(m[1]))
    expect(turns).toHaveLength(96)
    expect(turns[0]).toBe(0)
    expect(turns[95]).toBeCloseTo(356.25, 2)
  })

  it('gives every bar of a lobe its own phase of the wave', () => {
    const phases = [...css('flywheel.css').matchAll(/nth-child\(24n \+ (\d+)\) rect \{\s*--d: (-[\d.]+)s;/g)]
    expect(phases.map((m) => Number(m[1]))).toEqual(Array.from({ length: 23 }, (_, k) => k + 1))
    expect(new Set(phases.map((m) => m[2])).size).toBe(23)
    // The last phase stays inside one there-and-back of the 4.8 s wave.
    expect(Math.abs(Number(phases.at(-1)?.[2]))).toBeLessThan(9.6)
  })
})

// The public pages are compared before and after the client takes over the prerendered HTML
// (e2e/tests/public-takeover-cells.ts). Motion there must be CSS, not a state a script sets, and
// must stand still for a visitor who asked for less motion.
describe('the motion stylesheets', () => {
  it.each(['motion.css', 'flywheel.css'])('%s starts every moving rule under prefers-reduced-motion: no-preference', (name) => {
    const sheet = css(name).replace(/\/\*[\s\S]*?\*\//g, '')
    const calm = sheet.slice(0, sheet.indexOf('@media (prefers-reduced-motion: no-preference)'))
    // Before that block an animation may only be declared paused (the flywheel's still frame).
    for (const rule of calm.match(/animation:[^;]+;/g) ?? []) expect(rule).toMatch(/\bpaused;$/)
    expect(calm).not.toMatch(/animation-timeline/)
  })

  it('moves text and never fades or recolours it: only pictures change opacity', () => {
    // A block can rest half-way through its entrance (at the bottom edge of the screen, or in the
    // first frames of a list), so these keyframes must leave contrast alone. The others belong to
    // pictures: the playhead and veil, the dot, the flywheel, the burst.
    const all = ['motion.css', 'motion-app.css', 'motion-feedback.css'].map(css).join('\n')
    for (const name of ['reveal-in', 'card-arrive', 'badge-fill', 'row-in', 'notice-pop', 'check-pop', 'chart-in', 'draw-in']) {
      const body = new RegExp(`@keyframes ${name} \\{([\\s\\S]*?)\\n\\}`).exec(all)?.[1]
      expect(body, name).toBeDefined()
      expect(body, name).not.toMatch(/opacity|(^|[^-])color:|filter/)
    }
  })

  it('motion.css uses scroll-driven animations only where the browser has them', () => {
    const sheet = css('motion.css').replace(/\/\*[\s\S]*?\*\//g, '')
    const before = sheet.slice(0, sheet.indexOf('@supports (animation-timeline: view())'))
    expect(before).not.toMatch(/animation-timeline/)
    expect(sheet).toMatch(/animation-timeline: view\(/)
  })

  it('no public-page source sets a reveal or motion state from script', () => {
    const page = (path: string) => readFileSync(join(import.meta.dirname, '../..', path), 'utf8')
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
      expect(page(path), path).not.toMatch(/useEffect|useLayoutEffect|IntersectionObserver|setAttribute|classList/)
    }
  })
})
