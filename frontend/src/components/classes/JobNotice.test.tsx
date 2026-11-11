import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { I18nProvider } from '../../i18n/I18n'
import { RouterProvider } from '../../router/Router'
import { JobNotice } from './JobNotice'

const show = (kind: string) =>
  render(
    <I18nProvider initial="en">
      <RouterProvider initialLocation={{ pathname: '/en/classes/c1', search: '' }}>
        <JobNotice job={{ id: 'j1', kind, state: 'running' }} />
      </RouterProvider>
    </I18nProvider>,
  )

describe('JobNotice', () => {
  it.each([
    ['design_draft', /being designed/],
    ['refit_draft', /timing fixes are being applied/],
  ])('says what %s is doing and links to its progress page', (kind, words) => {
    show(kind)
    expect(screen.getByText(words)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'See progress' })).toHaveAttribute('href', '/en/jobs/j1')
  })

  it.each(['design_with_songs', 'change_songs', 'save_workout', 'revise_draft', 'interpret_request', 'something_new'])(
    'says nothing for %s, which has no progress page of its own',
    (kind) => {
      const { container } = show(kind)
      expect(container).toBeEmptyDOMElement()
    },
  )
})
