import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { I18nProvider } from '../../i18n/I18n'
import { LengthChoice } from './LengthChoice'

function Harness({ start = '30', maxMinutes = null }: { start?: string; maxMinutes?: number | null }) {
  const [length, setLength] = useState(start)
  return (
    <I18nProvider>
      <LengthChoice length={length} onChange={setLength} maxMinutes={maxMinutes} />
      <output data-testid="length">{length}</output>
    </I18nProvider>
  )
}

describe('LengthChoice', () => {
  it('is one named radio group of the common lengths and Other, with no number field yet', () => {
    render(<Harness />)
    expect(screen.getByRole('radiogroup', { name: 'Length' })).toBeInTheDocument()
    expect(screen.getAllByRole('radio').map((r) => r.textContent)).toEqual(['30 min', '45 min', '60 min', '90 min', 'Other'])
    expect(screen.getByRole('radio', { name: '30 min' })).toBeChecked()
    expect(screen.queryByLabelText('Length in minutes')).toBeNull()
  })

  it('picking a common length sets it', () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('radio', { name: '60 min' }))
    expect(screen.getByTestId('length')).toHaveTextContent('60')
    expect(screen.getByRole('radio', { name: '60 min' })).toBeChecked()
  })

  it('Other opens the number field with the current length and moves focus to it', () => {
    render(<Harness start="45" />)
    fireEvent.click(screen.getByRole('radio', { name: 'Other' }))
    const field = screen.getByLabelText('Length in minutes')
    expect(field).toHaveValue(45)
    expect(field).toHaveFocus()
    expect(screen.getByRole('radio', { name: 'Other' })).toBeChecked()
    fireEvent.change(field, { target: { value: '50' } })
    expect(screen.getByTestId('length')).toHaveTextContent('50')
    expect(screen.getByRole('radio', { name: 'Other' })).toBeChecked()
  })

  it('typing a common number into Other keeps the field open', () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('radio', { name: 'Other' }))
    fireEvent.change(screen.getByLabelText('Length in minutes'), { target: { value: '60' } })
    expect(screen.getByLabelText('Length in minutes')).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Other' })).toBeChecked()
  })

  it('picking a common length again closes the field', () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('radio', { name: 'Other' }))
    fireEvent.click(screen.getByRole('radio', { name: '90 min' }))
    expect(screen.queryByLabelText('Length in minutes')).toBeNull()
    expect(screen.getByTestId('length')).toHaveTextContent('90')
  })

  it('shows the number field at once for a length that is not a common one (a preset link)', () => {
    render(<Harness start="50" />)
    expect(screen.getByLabelText('Length in minutes')).toHaveValue(50)
    expect(screen.getByRole('radio', { name: 'Other' })).toBeChecked()
  })

  it('switches off the lengths over the plan cap and says the cap once', () => {
    render(<Harness maxMinutes={45} />)
    expect(screen.getByRole('radio', { name: '45 min' })).toBeEnabled()
    expect(screen.getByRole('radio', { name: '60 min' })).toBeDisabled()
    expect(screen.getAllByText('Your plan allows classes of up to 45 minutes.')).toHaveLength(1)
    fireEvent.click(screen.getByRole('radio', { name: 'Other' }))
    expect(screen.getAllByText('Your plan allows classes of up to 45 minutes.')).toHaveLength(1)
    expect(screen.getByLabelText('Length in minutes')).toHaveAttribute('max', '45')
  })

  it('says nothing about the cap when every length is allowed', () => {
    render(<Harness maxMinutes={180} />)
    expect(screen.queryByText(/Your plan allows/)).toBeNull()
  })
})
