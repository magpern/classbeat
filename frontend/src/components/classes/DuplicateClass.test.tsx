import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../api/client'
import { copyTitle, duplicateReasonText } from '../../api/classDuplicate'
import { DuplicateClass } from './DuplicateClass'

const mocks = vi.hoisted(() => ({
  duplicateClass: vi.fn(),
  getRevision: vi.fn(),
  navigate: vi.fn(),
}))
vi.mock('../../api/classDuplicate', async (importActual) => ({
  ...(await importActual<typeof import('../../api/classDuplicate')>()),
  duplicateClass: mocks.duplicateClass,
}))
vi.mock('../../api/classes', async (importActual) => ({
  ...(await importActual<typeof import('../../api/classes')>()),
  getRevision: mocks.getRevision,
}))
vi.mock('../../router/Router', async (importActual) => ({
  ...(await importActual<typeof import('../../router/Router')>()),
  useRouter: () => ({ navigate: mocks.navigate, pathname: '/classes/c1', search: '', hash: '' }),
}))

function refusal(status: number, detail: Record<string, unknown>): ApiError {
  return new ApiError(status, String(detail.reason), null, { detail })
}

function open(revisionId: string | null = 'r2') {
  render(<DuplicateClass classId="c1" title="Tuesday climb" revisionId={revisionId} versionNumber={2} />)
  fireEvent.click(screen.getByRole('button', { name: 'Duplicate Tuesday climb' }))
}

describe('DuplicateClass', () => {
  beforeEach(() => {
    mocks.duplicateClass.mockReset()
    mocks.getRevision.mockReset().mockResolvedValue({ raw: { name: 'x', segments: [] } })
    mocks.navigate.mockReset()
  })

  it('copies the version under the edited name and opens the new class', async () => {
    mocks.duplicateClass.mockResolvedValue({ classId: 'c9', revisionId: 'r9' })
    open()
    expect(screen.getByRole('dialog', { name: 'Duplicate this class' }).textContent).toContain(
      'Version 2 becomes version 1 of a new class',
    )
    const name = screen.getByLabelText('Name of the copy')
    expect((name as HTMLInputElement).value).toBe('Tuesday climb (copy)')
    fireEvent.change(name, { target: { value: '  Thursday climb ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Duplicate' }))
    await waitFor(() => {
      expect(mocks.navigate).toHaveBeenCalledWith('/classes/c9')
    })
    const [classId, revisionId, hash, title] = mocks.duplicateClass.mock.calls[0] as string[]
    expect([classId, revisionId, title]).toEqual(['c1', 'r2', 'Thursday climb'])
    expect(hash).toMatch(/^[0-9a-f]{64}$/)
    expect(mocks.getRevision).toHaveBeenCalledWith('c1', 'r2')
  })

  it('says the plan refusal in the usual sentence and stays open', async () => {
    mocks.duplicateClass.mockRejectedValue(
      refusal(409, { reason: 'quota', quota: 'classes', limit: 3, used: 3, period_end: '2026-12-01', plan: 'free' }),
    )
    open()
    fireEvent.click(screen.getByRole('button', { name: 'Duplicate' }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('You have used')
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(mocks.navigate).not.toHaveBeenCalled()
  })

  it('offers nothing for a class without a version', () => {
    render(<DuplicateClass classId="c1" title="Tuesday climb" revisionId={null} versionNumber={null} />)
    expect(screen.queryByRole('button', { name: /Duplicate/ })).toBeNull()
  })

  it('cannot be sent with an empty name', () => {
    open()
    fireEvent.change(screen.getByLabelText('Name of the copy'), { target: { value: '   ' } })
    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Duplicate' }).disabled).toBe(true)
  })
})

describe('duplicate words', () => {
  it('proposes a copy name within the server limit', () => {
    expect(copyTitle('Tuesday climb')).toBe('Tuesday climb (copy)')
    const long = copyTitle('x'.repeat(100))
    expect(long.length).toBeLessThanOrEqual(100)
    expect(long.endsWith(' (copy)')).toBe(true)
  })

  it('words every refusal', () => {
    expect(duplicateReasonText(refusal(409, { reason: 'not_ready' }))).toContain('no finished design')
    expect(duplicateReasonText(refusal(409, { reason: 'feature_off' }))).toContain('cannot be made right now')
    expect(duplicateReasonText(refusal(409, { reason: 'hash_mismatch' }))).toContain('changed elsewhere')
    expect(duplicateReasonText(refusal(404, { reason: 'not_found' }))).toContain('no longer exists')
    expect(duplicateReasonText(refusal(422, { reason: 'invalid_input' }))).toContain('1 to 100 characters')
    expect(
      duplicateReasonText(refusal(409, { reason: 'quota', quota: 'length', limit: 30, used: 45, plan: 'free' })),
    ).toContain('up to 30 minutes')
    expect(
      duplicateReasonText(refusal(409, { reason: 'quota', quota: 'feature', feature: 'games', plan: 'free' })),
    ).toContain('Your plan does not include')
  })
})
