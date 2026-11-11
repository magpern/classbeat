import { screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { zoneFill } from '../components/classes/zoneClasses'
import { resetCatalogues } from '../i18n/t'
import { clock } from '../lib/workout'
import { EXAMPLES, exampleKey, exampleProfile, exampleSeconds } from '../publicSite/examples'
import { disagreements } from '../test/exampleMeasures'
import { renderAt } from '../test/render'
import { translate } from '../i18n/t'

afterEach(() => {
  resetCatalogues()
})

const bars = () => [...document.querySelectorAll('svg [data-bar]')]
const rows = () => [...document.querySelectorAll('[data-testid="example-table"] tbody tr')]

describe.each(EXAMPLES.map((e) => [e.slug, e] as const))('/examples/%s', (slug, example) => {
  it('has one h1 and one set of minutes everywhere', () => {
    renderAt(`/examples/${slug}`)
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    const seconds = exampleSeconds(example)
    expect(seconds).toBe(example.brief.length_min * 60)
    expect(screen.getByTestId('example-length').textContent).toBe(`${String(seconds / 60)} minutes`)
    expect(screen.getByTestId('example-total').textContent).toBe(clock(seconds))
    expect(translate('en', exampleKey(slug, 'prompt'))).toContain(`${String(seconds / 60)} minute`)
    // The chart: the summary names the total and the last bar ends at the end of the axis.
    const chart = screen.getByRole('img', { name: new RegExp(clock(seconds)) })
    expect(chart.tagName.toLowerCase()).toBe('svg')
    const last = bars().at(-1)
    expect(Number(last?.getAttribute('x')) + Number(last?.getAttribute('width')) + 1).toBeCloseTo(600, 5)
    expect(bars().reduce((sum, b) => sum + Number(b.getAttribute('width')) + 1, 0)).toBeCloseTo(600 - 34, 5)
  })

  it('has a table that matches the chart bar for bar', () => {
    renderAt(`/examples/${slug}`)
    const flat = exampleProfile(example)
    expect(rows()).toHaveLength(bars().length)
    expect(rows()).toHaveLength(flat.length)
    flat.forEach(([zone, sec], i) => {
      expect(rows()[i]?.getAttribute('data-zone')).toBe(String(zone))
      expect(rows()[i]?.getAttribute('data-sec')).toBe(String(sec))
      expect(bars()[i]?.getAttribute('data-zone')).toBe(String(zone))
    })
    expect(rows().reduce((sum, r) => sum + Number(r.getAttribute('data-sec')), 0)).toBe(exampleSeconds(example))
  })

  it('shows the same numbers in the words as in the class, and no song', () => {
    renderAt(`/examples/${slug}`)
    expect(disagreements(screen.getByTestId('example-prompt').textContent, example)).toEqual([])
    expect(document.querySelector('[data-testid="song-strip"]')).toBeNull()
    expect(document.body.innerHTML).not.toMatch(/\sstyle=/)
    expect(screen.queryByTestId('marker-game')).toBeNull()
  })
})

describe('/examples', () => {
  it('lists four cards whose profile is the one on the detail page', () => {
    renderAt('/examples')
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    const cards = [...document.querySelectorAll('[data-example]')]
    expect(cards).toHaveLength(4)
    cards.forEach((card, i) => {
      const example = EXAMPLES[i]
      if (!example) throw new Error('no example')
      expect(within(card as HTMLElement).getByRole('link').getAttribute('href')).toBe(`/en/examples/${example.slug}`)
      const rects = [...card.querySelectorAll('svg rect')]
      const flat = exampleProfile(example)
      expect(rects).toHaveLength(flat.length)
      flat.forEach(([zone], j) => {
        expect(rects[j]?.getAttribute('class')).toBe(zoneFill(zone))
      })
      const label = card.querySelector('svg')?.getAttribute('aria-label') ?? ''
      expect(label).toContain(String(flat.length))
    })
  })

  it('says the examples are in English and carry no real song', () => {
    renderAt('/examples')
    expect(document.body.textContent).toMatch(/None names a real song/)
  })
})
