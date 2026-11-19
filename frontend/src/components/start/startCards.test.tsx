import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { I18nProvider } from '../../i18n/I18n'
import { RouterProvider } from '../../router/Router'
import { MusicWarning } from '../newclass/MusicWarning'
import { PlanLength } from './PlanLength'
import { StartAside } from './StartAside'

function show(node: React.ReactNode) {
  return render(
    <I18nProvider initial="en">
      <RouterProvider initialLocation={{ pathname: '/en/classes/new', search: '' }}>{node}</RouterProvider>
    </I18nProvider>,
  )
}

describe('start screen cards', () => {
  it('shows a missing MOWL connection as a warning inside "Music from", with the connect link', async () => {
    show(<MusicWarning reconnect={false} />)
    expect(await screen.findByRole('heading', { name: 'Music from' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Your music comes from MOWL.')
    expect(screen.getByRole('link', { name: 'Connect MOWL' })).toHaveAttribute('href', '/en/account')
  })

  it('says to reconnect when the MOWL sign-in expired', async () => {
    show(<MusicWarning reconnect />)
    expect(await screen.findByRole('link', { name: 'Connect MOWL again' })).toBeInTheDocument()
  })

  it('shows the example sketch and the three steps of what happens next', async () => {
    show(<StartAside />)
    expect(await screen.findByRole('img', { name: /Example class shape/ })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'What happens next' })).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(3)
  })
})

describe('the Length note', () => {
  it('shows a skeleton while the plan loads, the cap once known, and a plain sentence if it could not be read', async () => {
    const { rerender } = show(<PlanLength allowed={null} settled={false} />)
    expect(await screen.findByTestId('plan-length-loading')).toBeInTheDocument()
    const again = (node: React.ReactNode) => {
      rerender(
        <I18nProvider initial="en">
          <RouterProvider initialLocation={{ pathname: '/en/classes/new', search: '' }}>{node}</RouterProvider>
        </I18nProvider>,
      )
    }
    again(<PlanLength allowed={{ maxMinutes: 45, gates: { games: true, races: true, ftpTests: true } }} settled />)
    expect(screen.getByTestId('plan-length')).toHaveTextContent('up to 45 minutes')
    again(<PlanLength allowed={null} settled />)
    expect(screen.getByTestId('plan-length-unknown')).toBeInTheDocument()
  })
})
