import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { InterpretationItem } from '../../api/interpretationItems'
import { I18nProvider } from '../../i18n/I18n'
import type { SaveFlow } from '../classes/useSaveFlow'
import { SaveArea } from './SaveArea'

vi.mock('../classes/SavePanel', () => ({ SaveNotices: () => null }))

const OFF: InterpretationItem = {
  path: 'shape.2',
  origin: 'not_as_asked',
  reasonCode: 'shape_off',
  value: { kind: 'efforts', count: { min: 7, max: 7 } },
  found: 5,
  quote: null,
}
const FLOW = { mode: null, hasSaved: false, showUpdate: false, canWrite: true, savedId: null, setMode: () => undefined } as unknown as SaveFlow

function show(props: { notAsked?: readonly InterpretationItem[]; currentOrigin?: string | null; flow?: SaveFlow }) {
  return render(
    <I18nProvider initial="en">
      <SaveArea flow={props.flow ?? FLOW} chip="ready" currentOrigin={props.currentOrigin ?? 'design_with_songs'} differed={false} notAsked={props.notAsked} />
    </I18nProvider>,
  )
}

describe('SaveArea: Not as asked before Save', () => {
  it('lists what is not as asked above the Save button of a draft', () => {
    show({ notAsked: [OFF] })
    const box = screen.getByTestId('not-asked-before')
    expect(box.textContent).toContain('Section 3: you asked for 7 efforts; this version has 5')
    expect(screen.getByRole('button', { name: 'Save to MOWL' })).toBeEnabled()
  })

  it('shows it before Update workout too', () => {
    show({ notAsked: [OFF], flow: { ...FLOW, hasSaved: true, showUpdate: true, savedId: 'w1', updateOverwrites: false, again: null } })
    expect(screen.getByTestId('not-asked-before')).toBeInTheDocument()
  })

  it('shows nothing when the version is as asked, or when no Save is offered', () => {
    const view = show({ notAsked: [] })
    expect(screen.queryByTestId('not-asked-before')).toBeNull()
    view.unmount()
    show({ notAsked: [OFF], currentOrigin: 'sketch' })
    expect(screen.queryByTestId('not-asked-before')).toBeNull()
  })
})
