import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { PromptLayer } from './PromptLayer'

// The layer a guard prompt is shown in (#856): focus goes in and comes back, Tab stays inside,
// and a click on its backdrop is not a way out. The prompts' bodies: GuardProvider.test.tsx.

function Prompt() {
  return (
    <PromptLayer labelledBy="p-title">
      <h2 id="p-title">Confirm it is you</h2>
      <button>First</button>
      <button>Last</button>
    </PromptLayer>
  )
}

describe('PromptLayer', () => {
  it('moves focus into the prompt and gives it back to the control that had it', () => {
    const { rerender } = render(<button>Opener</button>)
    const opener = screen.getByRole('button', { name: 'Opener' })
    opener.focus()
    rerender(
      <>
        <button>Opener</button>
        <Prompt />
      </>,
    )
    expect(screen.getByRole('dialog', { name: 'Confirm it is you' })).toHaveFocus()
    rerender(<button>Opener</button>)
    expect(opener).toHaveFocus()
  })

  it('hands focus to the dialog still open below when the opener can no longer take it', () => {
    function Page({ prompt, disabled }: { prompt: boolean; disabled: boolean }) {
      return (
        <>
          <div role="dialog" aria-label="Below" tabIndex={-1}>
            <button disabled={disabled}>Busy</button>
          </div>
          {prompt ? <Prompt /> : null}
        </>
      )
    }
    const { rerender } = render(<Page prompt={false} disabled={false} />)
    screen.getByRole('button', { name: 'Busy' }).focus()
    rerender(<Page prompt disabled={false} />)
    rerender(<Page prompt disabled />)
    rerender(<Page prompt={false} disabled />)
    expect(screen.getByRole('dialog', { name: 'Below' })).toHaveFocus()
  })

  it('keeps Tab and Shift+Tab inside the prompt', async () => {
    render(<Prompt />)
    const first = screen.getByRole('button', { name: 'First' })
    const last = screen.getByRole('button', { name: 'Last' })
    last.focus()
    await userEvent.tab()
    expect(first).toHaveFocus()
    await userEvent.tab({ shift: true })
    expect(last).toHaveFocus()
  })

  it('does not go away on a click on its backdrop or on Escape', () => {
    render(<Prompt />)
    const dialog = screen.getByRole('dialog', { name: 'Confirm it is you' })
    const backdrop = dialog.parentElement!
    expect(backdrop).toHaveClass('overlay')
    fireEvent.pointerDown(backdrop)
    fireEvent.click(backdrop)
    fireEvent.keyDown(dialog, { key: 'Escape' })
    expect(screen.getByRole('dialog', { name: 'Confirm it is you' })).toBe(dialog)
    expect(dialog.contains(document.activeElement)).toBe(true)
  })
})
