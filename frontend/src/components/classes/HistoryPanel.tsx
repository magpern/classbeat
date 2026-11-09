import { useEffect, useState } from 'react'
import type { RevisionSummary } from '../../api/classes'
import {
  askedFor,
  compareFailureText,
  compareRevisions,
  describeComparison,
  getHistory,
  type History,
  type TimelineEntry,
} from '../../api/classHistory'
import { clock } from '../../lib/workout'
import { Button } from '../ui/button'
import { Chip, ChipButton } from '../ui/chip'
import { ListRow } from '../ui/list-row'
import { RevisionsPanel, VERSION_LIST, VERSION_ROW } from './RevisionsPanel'
import { TssValue } from './TssValue'
import { dateTime } from '../../i18n/format'
import { Trans, useT } from '../../i18n/I18n'

function when(iso: string): string {
  return dateTime(iso, '')
}

type Compared = { key: string; lines: string[]; title: string } | { key: string; failed: string }

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
  onCanChange,
  busy,
}: {
  classId: string
  revisions: RevisionSummary[]
  viewingId: string | null
  onView: (id: string) => void
  onUse: (revision: RevisionSummary) => void
  /** Told whether Change it works for this class, once the history says (the server refuses it otherwise). */
  onCanChange?: (can: boolean) => void
  busy: boolean
}) {
  const t = useT()
  // Read again whenever the versions or the current one change (a change, a pick).
  const key = revisions.map((r) => `${r.id}:${r.isCurrent ? '1' : '0'}`).join(',')
  const [loaded, setLoaded] = useState<{ key: string; history: History } | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [compared, setCompared] = useState<Compared | null>(null)
  const [readFailed, setReadFailed] = useState(false)
  useEffect(() => {
    const abort = new AbortController()
    getHistory(classId, abort.signal).then(
      (history) => {
        if (abort.signal.aborted) return
        setReadFailed(false)
        setLoaded({ key, history })
        onCanChange?.(history.canChange)
      },
      () => {
        if (!abort.signal.aborted) setReadFailed(true)
      },
    )
    return () => {
      abort.abort()
    }
    // onCanChange is a setter of the page; a new function must not read the history again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [classId, key])

  // While the history is read again for the same set of versions (Make current only moves the
  // flag), the last answer stays on screen with the flag taken from the page, so the list does not
  // fall back to the plain one and back. A different set of versions shows the plain list until read.
  // "The same" means every field of a version that both sides carry and the list renders (its
  // number, kind, parent, time and length), not only its id; the flag is the one thing taken from
  // the page.
  const sameVersions =
    loaded !== null &&
    loaded.history.revisions.length === revisions.length &&
    loaded.history.revisions.every((r) =>
      revisions.some(
        (s) =>
          s.id === r.id &&
          s.number === r.number &&
          s.origin === r.origin &&
          s.parentNumber === r.parentNumber &&
          s.createdAt === r.createdAt &&
          s.totalSec === r.totalSec,
      ),
    )
  const history =
    loaded === null
      ? null
      : loaded.key === key
        ? loaded.history
        : sameVersions
          ? {
              ...loaded.history,
              revisions: loaded.history.revisions.map((r) => ({
                ...r,
                isCurrent: revisions.find((s) => s.id === r.id)?.isCurrent ?? r.isCurrent,
              })),
            }
          : null
  if (history === null) {
    return (
      <>
        {readFailed ? (
          <p className="muted m-0" role="status" data-testid="history-unavailable">
            {t('class.history.unavailable')}
          </p>
        ) : null}
        <RevisionsPanel revisions={revisions} viewingId={viewingId} onView={onView} onUse={onUse} busy={busy} />
      </>
    )
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
    const title = t('class.history.compareTitle', { older: older.number, newer: newer.number })
    compareRevisions(classId, older.id, newer.id).then(
      (found) => {
        setCompared({ key: wanted, title, lines: describeComparison(found) })
      },
      (error: unknown) => {
        setCompared({ key: wanted, failed: compareFailureText(error) })
      },
    )
  }
  const shownComparison = compared !== null && compared.key === pairKey ? compared : null

  return (
    <section className="panel" aria-labelledby="revisions-title">
      <h2 id="revisions-title" className="panel__title">
        {t('class.versions')}
      </h2>
      <p className="muted m-0">{t('class.history.pick')}</p>
      {history.canChange || history.revisions.length === 0 ? null : (
        <p className="m-0" data-testid="cannot-change">
          {t('class.history.cannotChange')}
        </p>
      )}
      {history.unreadable > 0 ? (
        <p className="m-0" data-testid="unreadable-versions">
          {t('class.history.unreadable', { count: history.unreadable })}
        </p>
      ) : null}
      <ol className={VERSION_LIST} aria-label={t('class.history.list')}>
        {ordered.map((r) => {
          const summary = revisions.find((s) => s.id === r.id)
          const tss = r.tss
          return (
            <ListRow
              key={r.id}
              aria-current={r.id === viewingId ? 'true' : undefined}
              data-testid="history-entry"
              className={VERSION_ROW}
              title={<span>{t('class.version', { number: r.number })}</span>}
              badge={
                <>
                  {r.isCurrent ? <Chip tone="ok">{t('class.current')}</Chip> : null}
                  {r.saved === 'live' ? <Chip>{t('class.history.savedMowl')}</Chip> : null}
                  {r.saved === 'detached' ? <Chip>{t('class.history.savedEarlier')}</Chip> : null}
                  {r.fromSongs ? <Chip>{t('class.history.fromSongs')}</Chip> : null}
                </>
              }
              description={
                <>
                  <span className="block break-words text-text">{askedFor(r)}</span>
                  <span className="block">
                    {clock(r.totalSec)}
                    {tss !== null ? (
                      <>
                        ,{' '}
                        <Trans
                          id="class.history.tss"
                          slots={{ value: <TssValue value={tss} approximate={r.tssApproximate} /> }}
                        />
                      </>
                    ) : null}
                    {r.parentNumber !== null ? `, ${t('class.fromVersion', { number: r.parentNumber })}` : ''}
                    {when(r.createdAt) ? `, ${when(r.createdAt)}` : ''}
                  </span>
                </>
              }
              action={
                <span className="flex flex-wrap gap-2">
                  <ChipButton
                    pressed={chosen.includes(r.id)}
                    aria-label={t('class.history.compareAria', { number: r.number })}
                    onClick={() => {
                      toggle(r.id)
                    }}
                  >
                    {t('class.history.compare')}
                  </ChipButton>
                  <Button
                    variant="secondary"
                    aria-label={t(r.id === viewingId ? 'class.viewingVersionAria' : 'class.viewVersionAria', {
                      number: r.number,
                    })}
                    disabled={r.id === viewingId}
                    onClick={() => {
                      onView(r.id)
                    }}
                  >
                    {r.id === viewingId ? t('class.viewing') : t('class.view')}
                  </Button>
                  {r.isCurrent || summary === undefined ? null : (
                    <Button
                      aria-label={t('class.useVersionAria', { number: r.number })}
                      disabled={busy}
                      onClick={() => {
                        onUse(summary)
                      }}
                    >
                      {t('class.useThisVersion')}
                    </Button>
                  )}
                </span>
              }
            />
          )
        })}
      </ol>
      {older !== null && newer !== null ? (
        <p className="m-0">
          <Button variant="secondary" onClick={compare}>
            {t('class.history.compareButton', { older: older.number, newer: newer.number })}
          </Button>
        </p>
      ) : null}
      {shownComparison !== null ? (
        <section aria-labelledby="compare-title" className="flex flex-col gap-2" data-testid="comparison">
          {'failed' in shownComparison ? (
            <p role="alert" className="m-0">
              {shownComparison.failed}
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
