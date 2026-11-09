import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { I18nProvider } from '../../i18n/I18n'
import { SideCard } from './SideCard'

const SUMMARY = [
  { label: 'Length', value: '30 min' },
  { label: 'Effort', value: 'Moderate' },
]

function show(classesLeft: number | null, busy = false, theme = 'dark') {
  return render(
    <div data-theme={theme}>
      <I18nProvider>
        <form>
          <SideCard profile={[[2, 600]]} summary={SUMMARY} busy={busy} classesLeft={classesLeft} />
        </form>
      </I18nProvider>
    </div>,
  )
}

describe('SideCard', () => {
  it('is the sticky action bar region with the summary and the one submit button', () => {
    show(2)
    const region = screen.getByRole('region', { name: 'Your class so far' })
    expect(region.className).toContain('lg:sticky')
    expect(region.className).toContain('max-lg:fixed')
    expect(within(region).getByText('30 min')).toBeInTheDocument()
    expect(within(region).getByText('Uses 1 of your 2 classes left this month')).toBeInTheDocument()
    const submit = within(region).getByRole('button', { name: 'Design my class' })
    expect(submit).toHaveAttribute('type', 'submit')
    expect(within(region).getAllByRole('button')).toHaveLength(1)
  })

  it('keeps the sketch and the summary to the desktop and the button to both', () => {
    show(null)
    const region = screen.getByRole('region', { name: 'Your class so far' })
    expect(screen.getByText('Length').closest('.max-lg\\:hidden')).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Design my class' }).closest('.max-lg\\:hidden')).toBeNull()
    expect(region.querySelector('.h-bar')).toBeNull()
  })

  it('says when there are no classes left, and shows nothing while it is unknown', () => {
    const { unmount } = show(0)
    expect(screen.getByText('You have no classes left this month.')).toBeInTheDocument()
    unmount()
    show(null)
    expect(screen.queryByText(/classes left|no classes left/)).toBeNull()
  })

  it('disables the button while busy', () => {
    show(1, true)
    expect(screen.getByRole('button', { name: 'Design my class' })).toBeDisabled()
  })

  it.each(['dark', 'light'])('uses tokens only in the %s theme: no inline style, no colour literal', (theme) => {
    const { container } = show(1, false, theme)
    expect(container.innerHTML).not.toMatch(/style=|#[0-9a-f]{3,8}\b|rgb\(/i)
  })
})
