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
