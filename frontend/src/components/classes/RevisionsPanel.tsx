import type { RevisionSummary } from '../../api/classes'
import { originLabel } from '../../api/classMessages'
import { clock } from '../../lib/workout'
import { dateTime } from '../../i18n/format'
import { useT } from '../../i18n/I18n'
import { Button } from '../ui/button'
import { ListRow } from '../ui/list-row'

/** The list of versions as one card of rows; the row of the version in view has a brand edge. */
export const VERSION_LIST = 'm-0 flex list-none flex-col divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface p-0'
export const VERSION_ROW =
  'flex-col items-stretch gap-3 border-l-4 border-l-transparent aria-[current=true]:border-l-brand'

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
      <ul className={VERSION_LIST}>
        {ordered.map((r) => (
          <ListRow
            key={r.id}
            aria-current={r.id === viewingId ? 'true' : undefined}
            className={VERSION_ROW}
            title={<span>{t('class.version', { number: r.number })}</span>}
            badge={r.isCurrent ? <span className="tag">{t('class.current')}</span> : null}
            description={
              <>
                {originLabel(r.origin)}
                {r.parentNumber !== null ? `, ${t('class.fromVersion', { number: r.parentNumber })}` : ''},{' '}
                {t('class.revisions.segments', { count: r.segmentCount })}, {clock(r.totalSec)}
                {when(r.createdAt) ? `, ${when(r.createdAt)}` : ''}
              </>
            }
            action={
              <span className="flex flex-wrap gap-2">
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
                {r.isCurrent ? null : (
                  <Button
                    aria-label={t('class.useVersionAria', { number: r.number })}
                    disabled={busy}
                    onClick={() => {
                      onUse(r)
                    }}
                  >
                    {t('class.useThisVersion')}
                  </Button>
                )}
              </span>
            }
          />
        ))}
      </ul>
    </section>
  )
}
