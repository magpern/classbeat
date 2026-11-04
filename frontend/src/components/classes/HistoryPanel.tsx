import { useEffect, useState } from 'react'
import type { RevisionSummary } from '../../api/classes'
import {
  askedFor,
  compareRevisions,
  describeComparison,
  getHistory,
  type History,
  type TimelineEntry,
} from '../../api/classHistory'
import { clock } from '../../lib/workout'
import { Button } from '../ui/button'
import { Chip, ChipButton } from '../ui/chip'
import { RevisionsPanel } from './RevisionsPanel'
import { TssValue } from './TssValue'

function when(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString()
}

type Compared = { key: string; lines: string[]; title: string } | { key: string; failed: true }

/**
 * The class history (ADR 0007 amendment of 2026-11-03, part H): every version, newest first, with
 * what asked for it, when, its length and TSS, and whether it is the current one, the one saved in
 * MOWL and one made on approved songs. Two versions can be compared in plain words, and any version
 * can be viewed or made current (the page's own Use this version, ADR 0007 section 2). While the
 * history cannot be read, the plain versions list stands in.
 */
export function HistoryPanel({
  classId,
  revisions,
  viewingId,
  onView,
  onUse,
  busy,
}: {
  classId: string
  revisions: RevisionSummary[]
  viewingId: string | null
  onView: (id: string) => void
  onUse: (revision: RevisionSummary) => void
  busy: boolean
}) {
  // Read again whenever the versions or the current one change (a change, a pick).
  const key = revisions.map((r) => `${r.id}:${r.isCurrent ? '1' : '0'}`).join(',')
  const [loaded, setLoaded] = useState<{ key: string; history: History } | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [compared, setCompared] = useState<Compared | null>(null)
  useEffect(() => {
    const abort = new AbortController()
    getHistory(classId, abort.signal).then(
      (history) => {
        if (!abort.signal.aborted) setLoaded({ key, history })
      },
      () => undefined,
    )
    return () => {
      abort.abort()
    }
  }, [classId, key])

  const history = loaded !== null && loaded.key === key ? loaded.history : null
  if (history === null) {
    return <RevisionsPanel revisions={revisions} viewingId={viewingId} onView={onView} onUse={onUse} busy={busy} />
  }
  const ordered = [...history.revisions].sort((a, b) => b.number - a.number)
  const byId = new Map(history.revisions.map((r) => [r.id, r]))
  const chosen = selected.filter((id) => byId.has(id))
  const pair = chosen.length === 2 ? chosen.map((id) => byId.get(id) as TimelineEntry) : null
  const [older, newer] = pair === null ? [null, null] : [...pair].sort((a, b) => a.number - b.number)
  const pairKey = older !== null && newer !== null ? `${older.id}>${newer.id}` : ''

  const toggle = (id: string) => {
    setSelected((now) => (now.includes(id) ? now.filter((s) => s !== id) : [...now.filter((s) => byId.has(s)), id].slice(-2)))
  }
  const compare = () => {
    if (older === null || newer === null) return
    const wanted = pairKey
    const title = `Version ${String(older.number)} to version ${String(newer.number)}`
    compareRevisions(classId, older.id, newer.id).then(
      (found) => {
        setCompared({ key: wanted, title, lines: describeComparison(found) })
      },
      () => {
        setCompared({ key: wanted, failed: true })
      },
    )
  }
  const shownComparison = compared !== null && compared.key === pairKey ? compared : null

  return (
    <section className="panel" aria-labelledby="revisions-title">
      <h2 id="revisions-title" className="panel__title">
        Versions
      </h2>
      <p className="muted m-0">Pick two versions to compare them.</p>
      {history.canChange || history.revisions.length === 0 ? null : (
        <p className="m-0" data-testid="cannot-change">
          Change it is not available for this class right now: it has no music of its own, or its kind of class is
          switched off. You can still view, compare, use and save its versions.
        </p>
      )}
      <ol className="rows revisions" aria-label="Version history">
        {ordered.map((r) => {
          const summary = revisions.find((s) => s.id === r.id)
          const tss = r.tss
          return (
            <li key={r.id} aria-current={r.id === viewingId ? 'true' : undefined} data-testid="history-entry">
              <span className="flex min-w-0 flex-col gap-1">
                <span className="flex flex-wrap items-center gap-2">
                  <strong>Version {r.number}</strong>
                  {r.isCurrent ? <Chip tone="ok">Current</Chip> : null}
                  {r.saved === 'live' ? <Chip>Saved in MOWL</Chip> : null}
                  {r.saved === 'detached' ? <Chip>Saved to an earlier MOWL connection</Chip> : null}
                  {r.fromSongs ? <Chip>Approved songs</Chip> : null}
                </span>
                <span className="break-words">{askedFor(r)}</span>
                <span className="muted">
                  {clock(r.totalSec)}
                  {tss !== null ? (
                    <>
                      , TSS <TssValue value={tss} approximate={r.tssApproximate} />
                    </>
                  ) : null}
                  {r.parentNumber !== null ? `, from version ${String(r.parentNumber)}` : ''}
                  {when(r.createdAt) ? `, ${when(r.createdAt)}` : ''}
                </span>
              </span>
              <span className="flex flex-wrap gap-2">
                <ChipButton
                  pressed={chosen.includes(r.id)}
                  aria-label={`Compare version ${String(r.number)}`}
                  onClick={() => {
                    toggle(r.id)
                  }}
                >
                  Compare
                </ChipButton>
                <Button
                  variant="secondary"
                  aria-label={`${r.id === viewingId ? 'Viewing' : 'View'} version ${String(r.number)}`}
                  disabled={r.id === viewingId}
                  onClick={() => {
                    onView(r.id)
                  }}
                >
                  {r.id === viewingId ? 'Viewing' : 'View'}
                </Button>
                {r.isCurrent || summary === undefined ? null : (
                  <Button
                    aria-label={`Use this version ${String(r.number)}`}
                    disabled={busy}
                    onClick={() => {
                      onUse(summary)
                    }}
                  >
                    Use this version
                  </Button>
                )}
              </span>
            </li>
          )
        })}
      </ol>
      {older !== null && newer !== null ? (
        <p className="m-0">
          <Button variant="secondary" onClick={compare}>
            Compare versions {older.number} and {newer.number}
          </Button>
        </p>
      ) : null}
      {shownComparison !== null ? (
        <section aria-labelledby="compare-title" className="flex flex-col gap-2" data-testid="comparison">
          {'failed' in shownComparison ? (
            <p role="alert" className="m-0">
              The two versions could not be compared. Try again.
            </p>
          ) : (
            <>
              <h3 id="compare-title" className="m-0 text-base font-semibold">
                {shownComparison.title}
              </h3>
              <ul className="m-0 flex flex-col gap-1 pl-5">
                {shownComparison.lines.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </>
          )}
        </section>
      ) : null}
    </section>
  )
}
