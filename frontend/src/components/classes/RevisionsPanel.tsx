import type { RevisionSummary } from '../../api/classes'
import { originLabel } from '../../api/classMessages'
import { clock } from '../../lib/workout'

function when(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString()
}

/** The versions of the class, newest first; the current one is marked, one is open for viewing. */
export function RevisionsPanel({
  revisions,
  viewingId,
  onView,
  onUse,
  busy,
}: {
  revisions: RevisionSummary[]
  viewingId: string | null
  onView: (id: string) => void
  onUse: (revision: RevisionSummary) => void
  busy: boolean
}) {
  const ordered = [...revisions].sort((a, b) => b.number - a.number)
  return (
    <section className="panel" aria-labelledby="revisions-title">
      <h2 id="revisions-title" className="panel__title">
        Versions
      </h2>
      {ordered.length === 0 ? <p className="muted">There is no version yet.</p> : null}
      <ul className="rows revisions">
        {ordered.map((r) => (
          <li key={r.id} aria-current={r.id === viewingId ? 'true' : undefined}>
            <span>
              <strong>Version {r.number}</strong>
              {r.isCurrent ? <span className="tag"> Current</span> : null}
              <br />
              <span className="muted">
                {originLabel(r.origin)}
                {r.parentNumber !== null ? `, from version ${String(r.parentNumber)}` : ''}, {r.segmentCount}{' '}
                segments, {clock(r.totalSec)}
                {when(r.createdAt) ? `, ${when(r.createdAt)}` : ''}
              </span>
            </span>
            <span>
              <button
                type="button"
                className="button button--secondary"
                aria-label={`View version ${String(r.number)}`}
                disabled={r.id === viewingId}
                onClick={() => {
                  onView(r.id)
                }}
              >
                {r.id === viewingId ? 'Viewing' : 'View'}
              </button>
              {r.isCurrent ? null : (
                <button
                  type="button"
                  className="button"
                  aria-label={`Use version ${String(r.number)}`}
                  disabled={busy}
                  onClick={() => {
                    onUse(r)
                  }}
                >
                  Use this version
                </button>
              )}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}
