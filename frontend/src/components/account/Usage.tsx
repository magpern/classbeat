import { useEffect, useState } from 'react'
import { getUsage, type Usage as UsageData } from '../../api/usage'
import { useT } from '../../i18n/I18n'
import { integer, month as formatMonth } from '../../i18n/format'
import { Notice, Section } from '../forms'

/**
 * Plan and usage (roadmap section 8, ADR 0007 section 10): this month's classes designed and
 * revisions. Model tokens and cost are internal accounting and never shown to the user (#820);
 * the API still returns them. Months are UTC months.
 */
export function Usage() {
  const t = useT()
  const [usage, setUsage] = useState<UsageData | null>(null)
  const [failed, setFailed] = useState(false)
  const [round, setRound] = useState(0)

  useEffect(() => {
    const abort = new AbortController()
    getUsage(abort.signal).then(
      (found) => {
        setFailed(false)
        setUsage(found)
      },
      () => {
        if (!abort.signal.aborted) setFailed(true)
      },
    )
    return () => {
      abort.abort()
    }
  }, [round])

  if (usage === null) {
    return (
      <Section title={t('usage.title')}>
        {failed ? (
          <>
            <Notice kind="error">{t('usage.loadFailed')}</Notice>
            <button
              type="button"
              className="button"
              onClick={() => {
                setRound((n) => n + 1)
              }}
            >
              {t('common.tryAgain')}
            </button>
          </>
        ) : (
          <p>{t('common.loading')}</p>
        )}
      </Section>
    )
  }
  return (
    <Section title={t('usage.title')}>
      <p>{t('usage.period', { month: formatMonth(usage.month) })}</p>
      <dl className="facts" data-testid="usage-facts">
        <div>
          <dt>{t('usage.designed')}</dt>
          <dd>{integer(usage.classesDesigned)}</dd>
        </div>
        <div>
          <dt>{t('usage.revisions')}</dt>
          <dd>{integer(usage.revisions)}</dd>
        </div>
      </dl>
    </Section>
  )
}
