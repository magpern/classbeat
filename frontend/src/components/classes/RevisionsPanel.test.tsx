import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { RevisionSummary } from '../../api/classes'
import { RevisionsPanel } from './RevisionsPanel'

function rev(number: number, isCurrent: boolean): RevisionSummary {
  return {
    id: `r${String(number)}`,
    number,
    isCurrent,
    parentId: null,
    parentNumber: null,
    origin: 'design_draft',
    segmentCount: 4,
    intervalCount: 8,
    totalSec: 600,
    createdAt: '2026-01-01T10:00:00Z',
  }
}

describe('RevisionsPanel accessible names', () => {
  it('starts each name with the visible text (WCAG 2.5.3) and names the version', () => {
    render(
      <RevisionsPanel
        revisions={[rev(1, false), rev(2, true), rev(3, false)]}
        viewingId="r3"
        onView={vi.fn()}
        onUse={vi.fn()}
        busy={false}
      />,
    )
    for (const n of [1, 3]) {
      const use = screen.getByRole('button', { name: `Use this version ${String(n)}` })
      expect(use.textContent).toBe('Use this version')
      expect(use.getAttribute('aria-label')?.startsWith('Use this version')).toBe(true)
    }
    expect(screen.getAllByRole('button', { name: /^Use this version \d$/ })).toHaveLength(2)
    expect(screen.getByRole('button', { name: 'Viewing version 3' }).textContent).toBe('Viewing')
    expect(screen.getByRole('button', { name: 'View version 1' }).textContent).toBe('View')
  })
})
