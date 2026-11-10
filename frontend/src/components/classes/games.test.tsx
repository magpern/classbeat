import { render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { Revision } from '../../api/classes'
import { describeComparison, type Comparison } from '../../api/classHistory'
import { decodeDraftContent, type DraftSegment, type DraftSummary } from '../../api/jobs'
import { I18nProvider } from '../../i18n/I18n'
import { gameMarkerName } from '../../lib/games'
import { resolveSlot, type SlotId } from '../imagery/imageSlots'
import { DraftSummaryView } from './DraftSummaryView'
import { ProfileChart } from './ProfileChart'
import { RevisionView } from './RevisionView'

const LICENCE = 'Games need a studio with a commercial MOWL licence. This is not checked here.'

const warm = { sec: 300, zone: 2, rpm: [80, 90] as [number, number], standing: false, cue: '' }
const GAME_SEGMENTS: DraftSegment[] = [
  { name: 'Warm up', kind: 'warmup', intervals: [warm] },
  {
    name: 'Work',
    kind: 'main',
    intervals: [
      { ...warm, sec: 120, zone: 5, game: 'power_pull' },
      { ...warm, sec: 90, zone: 7, game: 'ghost_target' },
    ],
  },
]
const RUNNING: DraftSegment[] = [
  { name: 'Run', kind: 'main', intervals: [{ sec: 300, zone: 3, rpm: null, standing: false, gradient: 0, cue: '' }] },
]

const wrap = (ui: React.ReactNode) => render(<I18nProvider initial="en">{ui}</I18nProvider>)

afterEach(() => {
  document.documentElement.removeAttribute('data-theme')
})

function revision(segments: DraftSegment[]): Revision {
  return {
    id: 'r1',
    number: 1,
    parentId: null,
    parentNumber: null,
    origin: 'design_draft',
    createdAt: '2026-11-10T10:00:00Z',
    isCurrent: true,
    segmentCount: segments.length,
    intervalCount: 3,
    totalSec: 510,
    name: 'Hills',
    description: '',
    segments,
    unknownBpm: [],
    tssApproximate: false,
    ruleHits: [],
    notesUseCurrentRules: false,
    raw: {},
  }
}

describe('decoding a game', () => {
  it('reads the four games and the legacy one, and drops an unknown id', () => {
    const interval = (game: unknown) => ({ sec: 60, zone: 3, rpm: [80, 90], standing: false, cue: '', game })
    const content = decodeDraftContent({
      name: 'x',
      segments: [
        {
          name: 's',
          kind: 'main',
          intervals: [interval('racetrack'), interval('ghost_target'), interval('unknown:9'), interval(undefined)],
        },
      ],
    })
    expect(content.segments[0]?.intervals.map((i) => i.game)).toEqual(['racetrack', 'ghost_target', undefined, undefined])
  })
})

describe('the game marker on the profile', () => {
  it('marks a game with an icon whose accessible name has the game and its length', () => {
    wrap(<ProfileChart segments={GAME_SEGMENTS} title="Hills" songs={null} />)
    expect(screen.getByRole('img', { name: 'Power Pull game, 2 min' })).toHaveAttribute('data-game', 'power_pull')
    expect(screen.getAllByTestId('marker-game')).toHaveLength(2)
    expect(gameMarkerName('overflow', 90)).toBe('Overflow game, 1 min 30 s')
    expect(gameMarkerName('fast_lane', 45)).toBe('Fast Lane game, 45 s')
  })

  it('has no game marker for a class without a game', () => {
    wrap(<ProfileChart segments={[GAME_SEGMENTS[0] as DraftSegment]} title="Hills" songs={null} />)
    expect(screen.queryByTestId('marker-game')).toBeNull()
  })

  it('names the legacy game as no longer offered', () => {
    wrap(<ProfileChart segments={GAME_SEGMENTS} title="Hills" songs={null} />)
    expect(screen.getByRole('img', { name: 'Ghost Target (no longer offered) game, 1 min 30 s' })).toBeInTheDocument()
  })
})

describe('the game on the cards', () => {
  it('RevisionView shows the type name, a small illustration and one licence line', () => {
    setTheme('light')
    wrap(<RevisionView revision={revision(GAME_SEGMENTS)} musicChanged={false} />)
    const card = screen.getByRole('region', { name: 'Work' })
    const badges = within(card).getAllByTestId('game-badge')
    expect(badges.map((b) => b.textContent)).toEqual(['Power Pull', 'Ghost Target (no longer offered)'])
    const picture = badges[0]?.querySelector('img')
    expect(picture).toHaveAttribute('src', '/img/game-power-pull-light-480.jpg')
    expect(picture?.getAttribute('alt')).toMatch(/^Power Pull:/)
    // the legacy game is a name only: no illustration of a game that is no longer offered
    expect(badges[1]?.querySelector('img')).toBeNull()
    expect(screen.getAllByText(LICENCE)).toHaveLength(1)
  })

  it('DraftSummaryView shows the same', () => {
    setTheme('dark')
    const draft: DraftSummary = { number: 1, name: 'Hills', description: '', segments: GAME_SEGMENTS, unknownBpm: [] }
    wrap(<DraftSummaryView draft={draft} />)
    expect(screen.getAllByTestId('game-badge')).toHaveLength(2)
    expect(screen.getByAltText(/^Power Pull:/)).toHaveAttribute('src', '/img/game-power-pull-dark-480.jpg')
    expect(screen.getAllByText(LICENCE)).toHaveLength(1)
  })

  it('shows no game UI and no licence line for a class without a game', () => {
    wrap(<RevisionView revision={revision([GAME_SEGMENTS[0] as DraftSegment])} musicChanged={false} />)
    expect(screen.queryByTestId('game-badge')).toBeNull()
    expect(screen.queryByText(LICENCE)).toBeNull()
  })

  it('shows no game UI for a running class, even if a game were present', () => {
    const run = RUNNING.map((s) => ({ ...s, intervals: s.intervals.map((i) => ({ ...i, game: 'racetrack' as const })) }))
    wrap(<RevisionView revision={revision(run)} musicChanged={false} />)
    expect(screen.queryByTestId('game-badge')).toBeNull()
    expect(screen.queryByText(LICENCE)).toBeNull()
    const draft: DraftSummary = { number: 1, name: 'Run', description: '', segments: run, unknownBpm: [] }
    wrap(<DraftSummaryView draft={draft} />)
    expect(screen.queryByTestId('game-badge')).toBeNull()
  })
})

function setTheme(theme: 'dark' | 'light') {
  document.documentElement.setAttribute('data-theme', theme)
}

describe('the game illustration slots', () => {
  const slots: [SlotId, string][] = [
    ['gameRacetrack', 'game-racetrack'],
    ['gamePowerPull', 'game-power-pull'],
    ['gameFastLane', 'game-fast-lane'],
    ['gameOverflow', 'game-overflow'],
  ]
  it.each(slots)('%s has its own light and dark picture', (slot, name) => {
    expect(resolveSlot(slot, 'light')).toMatchObject({ set: { name: `${name}-light` }, dimmed: false })
    expect(resolveSlot(slot, 'dark')).toMatchObject({ set: { name: `${name}-dark` }, dimmed: false })
  })
})

describe('describeComparison with games', () => {
  const base: Comparison = {
    fromNumber: 1,
    toNumber: 2,
    segmentsAdded: 0,
    segmentsRemoved: 0,
    intervalsAdded: 0,
    intervalsRemoved: 0,
    intervalsChanged: 1,
    secondsBefore: 600,
    secondsAfter: 600,
    tssBefore: 50,
    tssAfter: 50,
    textChanged: false,
    unknownBpmBefore: 0,
    unknownBpmAfter: 0,
    unsureBefore: 0,
    unsureAfter: 0,
    sameSource: true,
    sameFingerprint: true,
    gameChanges: [],
  }
  it('names a changed, an added, a removed and a legacy game', () => {
    const lines = describeComparison({
      ...base,
      gameChanges: [
        { segment: 3, before: 'power_pull', after: 'overflow' },
        { segment: 2, before: null, after: 'racetrack' },
        { segment: 4, before: 'fast_lane', after: null },
        { segment: 5, before: 'ghost_target', after: 'racetrack' },
      ],
    })
    expect(lines).toContain('Segment 3: Power Pull to Overflow')
    expect(lines).toContain('Segment 2: Racetrack added')
    expect(lines).toContain('Segment 4: Fast Lane removed')
    expect(lines).toContain('Segment 5: Ghost Target (no longer offered) to Racetrack')
  })
  it('says nothing about games when none changed', () => {
    expect(describeComparison(base).some((l) => l.startsWith('Segment'))).toBe(false)
  })
})
