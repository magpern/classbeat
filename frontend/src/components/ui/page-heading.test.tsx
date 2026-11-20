import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PageHeading } from './page-heading'

describe('PageHeading', () => {
  it('renders the title as an h1 by default, with the page scale', () => {
    render(<PageHeading title="My classes" />)
    const h = screen.getByRole('heading', { level: 1, name: 'My classes' })
    expect(h.className).toContain('font-display')
    expect(h.className).toContain('sm:text-5xl')
  })

  it('uses the heading level and the section scale it is given', () => {
    render(<PageHeading title="Made" as="h2" size="section" titleId="made" />)
    const h = screen.getByRole('heading', { level: 2, name: 'Made' })
    expect(h.id).toBe('made')
    expect(h.className).toContain('text-2xl')
    expect(h.className).not.toContain('text-4xl')
  })

  it('shows the eyebrow and intro only when given', () => {
    const { rerender } = render(<PageHeading title="T" />)
    expect(screen.queryByText('EYE')).toBeNull()
    rerender(<PageHeading title="T" eyebrow="EYE" intro="Intro text" />)
    expect(screen.getByText('EYE').className).toContain('tracking-[0.14em]')
    expect(screen.getByText('Intro text')).toBeTruthy()
  })

  it('renders the actions slot', () => {
    render(<PageHeading title="T" actions={<button type="button">New</button>} />)
    expect(screen.getByRole('button', { name: 'New' })).toBeTruthy()
  })

  it('renders a lone title as the bare heading, without a wrapper', () => {
    const { container } = render(<PageHeading title="T" as="h2" size="section" />)
    expect(container.firstElementChild?.tagName).toBe('H2')
  })

  it('stacks the actions under a page title on a phone', () => {
    const { container } = render(<PageHeading title="T" actions={<button type="button">New</button>} />)
    const row = container.firstElementChild as HTMLElement
    expect(row.className).toContain('flex-wrap')
    expect((row.lastElementChild as HTMLElement).className).toContain('w-full')
  })

  it('keeps a section title and its actions on one row at every width', () => {
    const { container } = render(
      <PageHeading as="h2" size="section" title="Recent" actions={<a href="/classes">See all</a>} />,
    )
    const row = container.firstElementChild as HTMLElement
    expect(row.className).toContain('flex-nowrap')
    expect(row.className).toContain('items-baseline')
    expect(row.className).toContain('justify-between')
    expect(row.className).not.toContain('flex-wrap ')
    expect((row.firstElementChild as HTMLElement).className).toContain('min-w-0')
    const actions = row.lastElementChild as HTMLElement
    expect(actions.className).toContain('shrink-0')
    expect(actions.className).not.toContain('w-full')
  })
})
