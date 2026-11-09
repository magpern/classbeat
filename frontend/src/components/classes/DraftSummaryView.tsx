import { isRunningDraft, type DraftInterval, type DraftSummary } from '../../api/jobs'
import { kindLabel } from '../../api/classMessages'
import { useT } from '../../i18n/I18n'
import { inclineText } from '../../lib/profile'
import type { TFunction } from '../../i18n/t'

// A read-only view of what GET /api/jobs/{id}/revision returned. Every string is rendered as
// text by React; nothing is computed here beyond adding up the lengths that are shown.

function clock(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return `${String(minutes)}:${String(rest).padStart(2, '0')}`
}

function cadence(t: TFunction, rpm: DraftInterval['rpm']): string {
  if (rpm === null) return ''
  return rpm === 'beat' ? t('class.cadence.beat') : t('class.cadence.range', { low: rpm[0], high: rpm[1] })
}

export function DraftSummaryView({ draft }: { draft: DraftSummary }) {
  const t = useT()
  const total = draft.segments.reduce(
    (sum, segment) => sum + segment.intervals.reduce((inner, i) => inner + i.sec, 0),
    0,
  )
  const running = isRunningDraft(draft.segments)
  return (
    <article className="draft" aria-labelledby="draft-title">
      <h2 id="draft-title" className="draft__title">
        {draft.name}
      </h2>
      <p className="muted">
        {t('class.draft.summary', { number: draft.number, total: clock(total) })}
      </p>
      {draft.description ? <p>{draft.description}</p> : null}
      {draft.unknownBpm.length > 0 ? (
        <div className="notice" data-kind="info" role="status" data-testid="unknown-bpm">
          <p>
            {t('class.unknownBpm')}
          </p>
          <ul>
            {draft.unknownBpm.map((track) => (
              <li key={track.index} className="break-words">{track.title}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {draft.segments.map((segment, index) => (
        <section key={index} className="draft__segment" aria-label={segment.name}>
          <h3>
            {segment.name} <span className="muted">{kindLabel(segment.kind)}</span>
          </h3>
          <table className="draft__table">
            <thead>
              <tr>
                <th scope="col">{t('class.column.length')}</th>
                <th scope="col">{t('class.column.zone')}</th>
                {running ? (
                  <th scope="col">{t('class.column.incline')}</th>
                ) : (
                  <>
                    <th scope="col">{t('class.column.cadence')}</th>
                    <th scope="col">{t('class.column.position')}</th>
                  </>
                )}
                <th scope="col">{t('class.column.cue')}</th>
              </tr>
            </thead>
            <tbody>
              {segment.intervals.map((interval, row) => (
                <tr key={row}>
                  <td>{clock(interval.sec)}</td>
                  <td>{interval.zone}</td>
                  {running ? (
                    <td>{inclineText(interval.gradient)}</td>
                  ) : (
                    <>
                      <td>{cadence(t, interval.rpm)}</td>
                      <td>{interval.standing ? t('class.position.standing') : t('class.position.seated')}</td>
                    </>
                  )}
                  <td>{interval.cue}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </article>
  )
}
