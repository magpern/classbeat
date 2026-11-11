import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import type { WorkspaceTab } from '../components/classes/Workspace'
import { useGoToPanel } from './useGoToPanel'

// The panel exists only on the Class tab, as on a phone.
function Harness({ start }: { start: WorkspaceTab }) {
  const [tab, setTab] = useState<WorkspaceTab>(start)
  const goTo = useGoToPanel(tab, setTab)
  return (
    <>
      <button type="button" onClick={() => { goTo('panel') }}>
        go
      </button>
      {tab === 'class' ? <section id="panel" tabIndex={-1} data-testid="panel" /> : <p>Chat</p>}
    </>
  )
}

describe('useGoToPanel', () => {
  it('focuses the panel at once when the Class tab is open', () => {
    render(<Harness start="class" />)
    fireEvent.click(screen.getByRole('button', { name: 'go' }))
    expect(document.activeElement).toBe(screen.getByTestId('panel'))
  })

  it('opens the Class tab first, then focuses the panel once it has rendered', () => {
    render(<Harness start="chat" />)
    expect(screen.queryByTestId('panel')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'go' }))
    expect(document.activeElement).toBe(screen.getByTestId('panel'))
  })
})
