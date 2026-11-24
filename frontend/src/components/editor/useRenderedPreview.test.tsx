import { render, screen, waitFor } from '@testing-library/react'
import { useLayoutEffect } from 'react'
import { describe, expect, it } from 'vitest'
import { CLASS_PATH } from '../../test/classFixtures'
import { fakeApi, json } from '../../test/fakeApi'
import { CLASS_ID, SESSION_ROUTES } from '../../test/jobFixtures'
import { useRenderedPreview } from './useRenderedPreview'

const SESSIONS = `POST ${CLASS_PATH}/preview/sessions`

// A press that arrives before the hook's passive effects ran (a layout effect runs first, as a click can on a
// loaded machine) is not dropped: it starts the session as any other Play.
function Probe() {
  const preview = useRenderedPreview({ classId: CLASS_ID, mixEndMs: 600_000, audioKey: 'k' })
  useLayoutEffect(() => {
    preview.audition(0, 0, 0)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, before the passive effects
  }, [])
  return <p data-testid="status">{preview.state.status + ':' + (preview.state.status === 'error' ? (preview.state.reason ?? '') : '')}</p>
}

describe('useRenderedPreview', () => {
  it('starts the session for a press that comes before its effects', async () => {
    const api = fakeApi({ ...SESSION_ROUTES, [SESSIONS]: () => json({ detail: { reason: 'not_found' } }, 404) })
    render(<Probe />)
    await waitFor(() => { expect(api.to('POST', `${CLASS_PATH}/preview/sessions`)).toHaveLength(1) })
    expect(await screen.findByTestId('status')).toHaveTextContent('error:unavailable')
    expect(document.querySelectorAll('audio')).toHaveLength(2) // one transport, its two elements
  })
})
