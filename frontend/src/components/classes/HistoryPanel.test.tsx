import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { RevisionSummary } from '../../api/classes'
import { ApiError } from '../../api/client'
import {
  askedFor,
  describeComparison,
  type Comparison,
  type History,
  type TimelineEntry,
} from '../../api/classHistory'
import { HistoryPanel } from './HistoryPanel'

const api = vi.hoisted(() => ({ getHistory: vi.fn(), compareRevisions: vi.fn() }))
vi.mock('../../api/classHistory', async (importActual) => ({
  ...(await importActual<typeof import('../../api/classHistory')>()),
  getHistory: api.getHistory,
  compareRevisions: api.compareRevisions,
}))

function entry(number: number, more: Partial<TimelineEntry> = {}): TimelineEntry {
  return {
    id: `r${String(number)}`,
    number,
    parentNumber: number > 1 ? number - 1 : null,
    origin: number === 1 ? 'design_draft' : 'revise_draft',
    createdAt: '2026-11-03T10:00:00Z',
    isCurrent: false,
    contentHash: 'ab'.repeat(32),
    segmentCount: 4,
    intervalCount: 8,
    totalSec: 2700,
    tss: 52.5,
    tssApproximate: false,
    request: null,
    saved: 'none',
    fromSongs: false,
    duplicatedFrom: null,
    ...more,
  }
}

function summary(e: TimelineEntry): RevisionSummary {
  return {
    id: e.id,
    number: e.number,
    parentId: null,
    parentNumber: e.parentNumber,
    origin: e.origin,
    createdAt: e.createdAt,
    isCurrent: e.isCurrent,
    segmentCount: e.segmentCount,
    intervalCount: e.intervalCount,
    totalSec: e.totalSec,
  }
}

const COMPARISON: Comparison = {
  fromNumber: 1,
  toNumber: 2,
  segmentsAdded: 0,
  segmentsRemoved: 0,
  intervalsAdded: 1,
  intervalsRemoved: 0,
  intervalsChanged: 2,
  secondsBefore: 2700,
  secondsAfter: 2880,
  tssBefore: 52.5,
  tssAfter: 60,
  textChanged: false,
  unknownBpmBefore: 0,
  unknownBpmAfter: 0,
  unsureBefore: 0,
  unsureAfter: 0,
  sameSource: true,
  sameFingerprint: true,
}

const ENTRIES = [
  entry(1, { saved: 'detached' }),
  entry(2, { request: 'Make the climbs longer', isCurrent: true, saved: 'live' }),
  entry(3, { origin: 'design_draft', fromSongs: true, parentNumber: 2 }),
]
const HISTORY: History = { sport: 'cycling', canChange: true, revisions: ENTRIES, unreadable: 0 }

function panel(props: { busy?: boolean; onCanChange?: (can: boolean) => void; onUse?: () => void; onView?: () => void; revisions?: RevisionSummary[] } = {}) {
  return (
    <HistoryPanel
      classId="c1"
      revisions={props.revisions ?? ENTRIES.map(summary)}
      viewingId="r2"
      onView={props.onView ?? vi.fn()}
      onUse={props.onUse ?? vi.fn()}
      onCanChange={props.onCanChange}
      busy={props.busy ?? false}
    />
  )
}

function show(onUse = vi.fn(), onView = vi.fn(), more: { busy?: boolean; onCanChange?: (can: boolean) => void } = {}) {
  const view = render(panel({ onUse, onView, ...more }))
  return { onUse, onView, view }
}

describe('HistoryPanel', () => {
  beforeEach(() => {
    api.getHistory.mockReset()
    api.compareRevisions.mockReset()
  })

  it('shows each version with what asked for it, its numbers and where it stands', async () => {
    api.getHistory.mockResolvedValue(HISTORY)
    show()
    const rows = await screen.findAllByTestId('history-entry')
    expect(rows.map((r) => within(r).getByText(/^Version \d$/).textContent)).toEqual([
      'Version 3',
      'Version 2',
      'Version 1',
    ])
    const [r3, r2, r1] = rows as [HTMLElement, HTMLElement, HTMLElement]
    expect(within(r2).getByText('Current')).toBeTruthy()
    expect(within(r2).getByText('Saved in MOWL')).toBeTruthy()
    expect(within(r2).getByText('You asked: “Make the climbs longer”')).toBeTruthy()
    expect(within(r1).getByText('First design')).toBeTruthy()
    expect(within(r1).getByText('Saved to an earlier MOWL connection')).toBeTruthy()
    expect(within(r3).getByText('Approved songs')).toBeTruthy()
    expect(r1.textContent).toContain('45:00')
    expect(r1.textContent).toContain('TSS 52.5')
    expect(api.getHistory).toHaveBeenCalledWith('c1', expect.anything())
  })

  it('makes an older version current through the page and views any version', async () => {
    api.getHistory.mockResolvedValue(HISTORY)
    const { onUse, onView } = show()
    await screen.findAllByTestId('history-entry')
    fireEvent.click(screen.getByRole('button', { name: 'Use this version 1' }))
    expect(onUse).toHaveBeenCalledWith(expect.objectContaining({ id: 'r1', number: 1 }))
    expect(screen.queryByRole('button', { name: 'Use this version 2' })).toBeNull() // current
    fireEvent.click(screen.getByRole('button', { name: 'View version 3' }))
    expect(onView).toHaveBeenCalledWith('r3')
  })

  it('compares two picked versions, older to newer, in plain words', async () => {
    api.getHistory.mockResolvedValue(HISTORY)
    api.compareRevisions.mockResolvedValue(COMPARISON)
    show()
    await screen.findAllByTestId('history-entry')
    fireEvent.click(screen.getByRole('button', { name: 'Compare version 2' }))
    expect(screen.queryByRole('button', { name: /^Compare versions/ })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Compare version 1' }))
    expect(screen.getByRole('button', { name: 'Compare version 1' }).getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(screen.getByRole('button', { name: 'Compare versions 1 and 2' }))
    const result = await screen.findByTestId('comparison')
    expect(api.compareRevisions).toHaveBeenCalledWith('c1', 'r1', 'r2')
    expect(within(result).getByRole('heading', { name: 'Version 1 to version 2' })).toBeTruthy()
    expect(result.textContent).toContain('2 intervals changed, 1 interval added.')
    expect(result.textContent).toContain('Length: 45:00 to 48:00 (3:00 longer).')
    expect(result.textContent).toContain('TSS: 52.5 to 60.')
    expect(result.textContent).toContain('Music: the same.')
  })

  it('picking a third version drops the first and a failed comparison says so', async () => {
    api.getHistory.mockResolvedValue(HISTORY)
    api.compareRevisions.mockRejectedValue(new Error('down'))
    show()
    for (const n of [1, 2, 3]) {
      fireEvent.click(await screen.findByRole('button', { name: `Compare version ${String(n)}` }))
    }
    expect(screen.getByRole('button', { name: 'Compare version 1' }).getAttribute('aria-pressed')).toBe('false')
    fireEvent.click(screen.getByRole('button', { name: 'Compare versions 2 and 3' }))
    expect((await screen.findByRole('alert')).textContent).toContain('could not be compared')
  })

  it('falls back to the plain versions list while the history cannot be read', async () => {
    api.getHistory.mockRejectedValue(new Error('down'))
    show()
    await waitFor(() => {
      expect(api.getHistory).toHaveBeenCalled()
    })
    expect(screen.getByRole('button', { name: 'Use this version 1' })).toBeTruthy()
    expect(screen.queryByTestId('history-entry')).toBeNull()
  })
})

describe('HistoryPanel states', () => {
  beforeEach(() => {
    api.getHistory.mockReset()
    api.compareRevisions.mockReset()
  })

  it('disables Use this version while the page is busy (a task is running or a switch is under way)', async () => {
    api.getHistory.mockResolvedValue(HISTORY)
    const { onUse } = show(vi.fn(), vi.fn(), { busy: true })
    await screen.findAllByTestId('history-entry')
    const use = screen.getByRole('button', { name: 'Use this version 1' })
    expect(use).toBeDisabled()
    fireEvent.click(use)
    expect(onUse).not.toHaveBeenCalled()
  })

  it('says how many versions can no longer be read, and nothing when all can', async () => {
    api.getHistory.mockResolvedValue({ ...HISTORY, unreadable: 2 })
    const first = show()
    expect((await screen.findByTestId('unreadable-versions')).textContent).toBe(
      '2 earlier versions can no longer be read and are not listed.',
    )
    first.view.unmount()
    api.getHistory.mockResolvedValue({ ...HISTORY, unreadable: 1 })
    const second = show()
    expect((await screen.findByTestId('unreadable-versions')).textContent).toBe(
      '1 earlier version can no longer be read and is not listed.',
    )
    second.view.unmount()
    api.getHistory.mockResolvedValue(HISTORY)
    show()
    await screen.findAllByTestId('history-entry')
    expect(screen.queryByTestId('unreadable-versions')).toBeNull()
  })

  it('tells the page whether Change it works', async () => {
    const onCanChange = vi.fn()
    api.getHistory.mockResolvedValue({ ...HISTORY, canChange: false })
    show(vi.fn(), vi.fn(), { onCanChange })
    await screen.findAllByTestId('history-entry')
    expect(onCanChange).toHaveBeenLastCalledWith(false)
  })

  it('says which way a comparison failed', async () => {
    api.getHistory.mockResolvedValue(HISTORY)
    show()
    for (const n of [1, 2]) {
      fireEvent.click(await screen.findByRole('button', { name: `Compare version ${String(n)}` }))
    }
    api.compareRevisions.mockRejectedValue(new ApiError(404, 'not_found', null, undefined))
    fireEvent.click(screen.getByRole('button', { name: 'Compare versions 1 and 2' }))
    expect((await screen.findByRole('alert')).textContent).toBe(
      'One of these versions is no longer there.',
    )
    api.compareRevisions.mockRejectedValue(new ApiError(500, 'internal', null, undefined))
    fireEvent.click(screen.getByRole('button', { name: 'Compare versions 1 and 2' }))
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toBe(
        'One of these versions can no longer be read, so the two cannot be compared.',
      )
    })
  })

  it('says so when the history cannot be read, next to the plain list', async () => {
    api.getHistory.mockRejectedValue(new Error('down'))
    show()
    expect((await screen.findByTestId('history-unavailable')).textContent).toContain('could not be read')
    expect(screen.getByRole('button', { name: 'Use this version 1' })).toBeTruthy()
  })

  it('keeps the timeline while it is read again after Make current, with the new current flag', async () => {
    api.getHistory.mockResolvedValueOnce(HISTORY)
    const { view } = show()
    await screen.findAllByTestId('history-entry')
    // Make current moves the flag only: the same versions, the history read again (never answered here).
    api.getHistory.mockReturnValue(new Promise(() => undefined))
    const moved = ENTRIES.map((e) => summary({ ...e, isCurrent: e.number === 1 }))
    view.rerender(panel({ revisions: moved }))
    const rows = screen.getAllByTestId('history-entry')
    expect(rows).toHaveLength(3)
    expect(within(rows[2]!).getByText('Current')).toBeTruthy()
    expect(within(rows[1]!).queryByText('Current')).toBeNull()
    expect(screen.queryByRole('list', { name: 'Versions' })).toBeNull()
  })

  it('shows the plain list while a new set of versions is read', async () => {
    api.getHistory.mockResolvedValueOnce(HISTORY)
    const { view } = show()
    await screen.findAllByTestId('history-entry')
    api.getHistory.mockReturnValue(new Promise(() => undefined))
    view.rerender(panel({ revisions: [...ENTRIES.map(summary), summary(entry(4))] }))
    expect(screen.queryByTestId('history-entry')).toBeNull()
  })
})

describe('HistoryPanel when Change it is not available', () => {
  beforeEach(() => {
    api.getHistory.mockReset()
  })

  it('says so for any class whose history cannot be changed, and not otherwise', async () => {
    api.getHistory.mockResolvedValue({ ...HISTORY, canChange: false })
    show()
    const note = await screen.findByTestId('cannot-change')
    expect(note.textContent).toContain('Change it is not available for this class right now')
    expect(note.textContent).not.toContain('This copy')
  })

  it('shows no note when the class can be changed', async () => {
    api.getHistory.mockResolvedValue(HISTORY)
    show()
    await screen.findAllByTestId('history-entry')
    expect(screen.queryByTestId('cannot-change')).toBeNull()
  })
})

describe('history words', () => {
  it('says what asked for a version', () => {
    expect(askedFor(entry(1))).toBe('First design')
    expect(askedFor(entry(2, { request: 'Easier' }))).toBe('You asked: “Easier”')
    expect(askedFor(entry(2))).toBe('Changed on request')
    const dup = { classId: 'c0', classTitle: 'Tuesday climb', number: 3 }
    expect(askedFor(entry(1, { origin: 'duplicate', duplicatedFrom: dup }))).toBe(
      'Duplicated from “Tuesday climb”, version 3',
    )
    const gone = { classId: null, classTitle: null, number: 3 }
    expect(askedFor(entry(1, { origin: 'duplicate', duplicatedFrom: gone }))).toBe(
      'Duplicated from a class that is gone',
    )
  })

  it('describes an unchanged workout, a text-only change and different music', () => {
    const same = { ...COMPARISON, intervalsAdded: 0, intervalsChanged: 0, secondsAfter: 2700, tssAfter: 52.5 }
    expect(describeComparison(same)[0]).toBe('The workouts are the same.')
    expect(describeComparison({ ...same, textChanged: true })[0]).toBe('Only names, descriptions or cues differ.')
    expect(describeComparison({ ...same, sameSource: false, sameFingerprint: false })).toContain(
      'Music: different music.',
    )
    expect(describeComparison({ ...same, sameFingerprint: false })).toContain(
      'Music: the same source, but its songs changed.',
    )
    expect(describeComparison({ ...same, unknownBpmAfter: 2 })).toContain('Timing warnings: 0 to 2.')
  })
})
