import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { Revision } from '../../api/classes'
import type { DraftSegment } from '../../api/jobs'
import { I18nProvider } from '../../i18n/I18n'
import { RevisionView } from './RevisionView'
import { versionLine } from './versionLine'
import { t } from '../../i18n/t'

const segments: DraftSegment[] = [
  {
    name: 'Climb',
    kind: 'main',
    intervals: [{ sec: 600, zone: 5, rpm: [60, 70], standing: true, cue: '' }],
  },
]

const revision: Revision = {
  id: 'r1',
  number: 1,
  parentId: null,
  parentNumber: null,
  origin: 'design_draft',
  createdAt: '2026-11-10T10:00:00Z',
  isCurrent: true,
  segmentCount: 1,
  intervalCount: 1,
  totalSec: 600,
  name: 'Tuesday climb',
  description: 'One long climb.',
  segments,
  unknownBpm: [],
  tssApproximate: false,
  ruleHits: [],
  notesUseCurrentRules: false,
  raw: {},
  interpretation: null,
}

const wrap = (pageTitle?: string, name = revision.name) =>
  render(
    <I18nProvider initial="en">
      <RevisionView revision={{ ...revision, name }} musicChanged={false} pageTitle={pageTitle} />
    </I18nProvider>,
  )

describe('RevisionView, Afterburn', () => {
  it('shows length, TSS, segments and hardest zone as stat tiles', () => {
    const { container } = wrap()
    const tiles = container.querySelector('dl[aria-label="Class figures"]') as HTMLElement
    expect(tiles).not.toBeNull()
    expect(within(tiles).getByTestId('duration')).toHaveTextContent('10:00')
    expect(within(tiles).getByText('Segments')).toBeInTheDocument()
    expect(within(tiles).getByText('Hardest zone')).toBeInTheDocument()
    expect(tiles).toHaveTextContent('Z5')
    expect(tiles).toHaveTextContent('VO2max')
  })

  it('keeps the name and version line on its own', () => {
    wrap()
    expect(screen.getByRole('heading', { level: 2, name: 'Tuesday climb' })).toBeInTheDocument()
    expect(screen.getByText('Version 1, first design')).toBeInTheDocument()
  })

  it('drops the repeated title but keeps the summary sentence when the page has the title', () => {
    wrap('Tuesday climb')
    expect(screen.queryByRole('heading', { level: 2, name: 'Tuesday climb' })).toBeNull()
    expect(screen.queryByText('Version 1, first design')).toBeNull()
    expect(screen.getByText('One long climb.')).toBeInTheDocument()
    expect(screen.getByRole('article', { name: 'Tuesday climb' })).toBeInTheDocument()
  })

  it('still names an older version whose name differs from the page title', () => {
    wrap('Tuesday climb', 'Tuesday climb, longer')
    expect(screen.getByRole('heading', { level: 2, name: 'Tuesday climb, longer' })).toBeInTheDocument()
    expect(screen.queryByText('Version 1, first design')).toBeNull()
  })

  it('draws the section headings as headings', () => {
    wrap()
    expect(screen.getByRole('heading', { level: 3, name: 'Time in each zone' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 3, name: 'Song by song' })).toBeInTheDocument()
  })

  it('words the version line', () => {
    expect(versionLine(t, revision)).toBe('Version 1, first design')
  })
})
