import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { REV1, classRoutes, revisionBody } from '../test/classFixtures'
import { fakeApi } from '../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES } from '../test/jobFixtures'
import { renderAt } from '../test/render'

// The class page header (ClassBody > ClassHeader) lists what the current version does not do as asked, before Save.

const OFF = {
  path: 'shape.2',
  origin: 'not_as_asked',
  reason_code: 'shape_off',
  value: { kind: 'efforts', count: { min: 7, max: 7 } },
  found: 5,
}

function withInterpretation(interpretation: unknown[]) {
  return {
    ...SESSION_ROUTES,
    ...classRoutes({ revisionBodies: { [REV1]: revisionBody(REV1, 1, undefined, { provenance: { origin: 'design_draft', interpretation } }) } }),
  }
}

describe('the class page: Not as asked before Save', () => {
  it('lists a shape_off item of the current version', async () => {
    fakeApi(withInterpretation([OFF]))
    renderAt(`/classes/${CLASS_ID}`)
    const box = await screen.findByTestId('not-asked-before')
    expect(box.textContent).toContain('Section 3: you asked for 7 efforts; this version has 5')
  })

  it('shows nothing when the version is as asked', async () => {
    fakeApi(withInterpretation([{ ...OFF, origin: 'explicit', reason_code: null }]))
    renderAt(`/classes/${CLASS_ID}`)
    expect(await screen.findByRole('heading', { name: /Tuesday climb/ })).toBeInTheDocument()
    expect(screen.queryByTestId('not-asked-before')).toBeNull()
  })
})
