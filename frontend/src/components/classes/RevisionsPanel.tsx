import type { RevisionSummary } from '../../api/classes'
import { originLabel } from '../../api/classMessages'
import { clock } from '../../lib/workout'
import { dateTime } from '../../i18n/format'
import { useT } from '../../i18n/I18n'

function when(iso: string): string {
  return dateTime(iso, '')
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
  const t = useT()
  const ordered = [...revisions].sort((a, b) => b.number - a.number)
  return (
    <section className="panel" aria-labelledby="revisions-title">
      <h2 id="revisions-title" className="panel__title">
        {t('class.versions')}
      </h2>
      {ordered.length === 0 ? <p className="muted">{t('class.revisions.none')}</p> : null}
      <ul className="rows revisions">
        {ordered.map((r) => (
          <li key={r.id} aria-current={r.id === viewingId ? 'true' : undefined}>
            <span>
              <strong>{t('class.version', { number: r.number })}</strong>
              {r.isCurrent ? <span className="tag"> {t('class.current')}</span> : null}
              <br />
              <span className="muted">
                {originLabel(r.origin)}
                {r.parentNumber !== null ? `, ${t('class.fromVersion', { number: r.parentNumber })}` : ''},{' '}
                {t('class.revisions.segments', { count: r.segmentCount })}, {clock(r.totalSec)}
                {when(r.createdAt) ? `, ${when(r.createdAt)}` : ''}
              </span>
            </span>
            <span>
              <button
                type="button"
                className="button button--secondary"
                aria-label={t(r.id === viewingId ? 'class.viewingVersionAria' : 'class.viewVersionAria', {
                  number: r.number,
                })}
                disabled={r.id === viewingId}
                onClick={() => {
                  onView(r.id)
                }}
              >
                {r.id === viewingId ? t('class.viewing') : t('class.view')}
              </button>
              {r.isCurrent ? null : (
                <button
                  type="button"
                  className="button"
                  aria-label={t('class.useVersionAria', { number: r.number })}
                  disabled={busy}
                  onClick={() => {
                    onUse(r)
                  }}
                >
                  {t('class.useThisVersion')}
                </button>
              )}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}
