import { useEffect, useState } from 'react'
import { getUsage, type Usage as UsageData } from '../../api/usage'
import type { CatalogueKey } from '../../i18n/en'
import { useT } from '../../i18n/I18n'
import { integer, month as formatMonth, usdMicros } from '../../i18n/format'
import { Notice, Section } from '../forms'

// What the figure leaves out, as the backend's `notes` codes (ADR 0007 section 10).
const NOTES: Record<string, CatalogueKey> = {
  estimate_at_list_prices: 'usage.note.estimate_at_list_prices',
  sdk_retries: 'usage.note.sdk_retries',
  lost_lease_turn: 'usage.note.lost_lease_turn',
  failed_stream_turn: 'usage.note.failed_stream_turn',
}

const KINDS: Record<string, CatalogueKey> = {
  design_draft: 'usage.kind.design_draft',
  revise_draft: 'usage.kind.revise_draft',
}

export function dollars(micros: number): string {
  return usdMicros(micros)
}

/**
 * Plan and usage (roadmap section 8, ADR 0007 section 10): this month's tokens, estimated cost,
 * classes designed and revisions, and what the figure leaves out. Months are UTC months.
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
  const { total } = usage
  const tokens = total.inputTokens + total.outputTokens + total.cacheReadTokens + total.cacheWriteTokens
  const reused = total.cacheReadTokens + total.cacheWriteTokens
  return (
    <Section title={t('usage.title')}>
      <p>{t('usage.period', { month: formatMonth(usage.month) })}</p>
      <dl className="facts" data-testid="usage-facts">
        <div>
          <dt>{t('usage.tokens')}</dt>
          <dd>{integer(tokens)}</dd>
        </div>
        <div>
          <dt>{t('usage.cost')}</dt>
          <dd>{dollars(total.costMicros)}</dd>
        </div>
        <div>
          <dt>{t('usage.designed')}</dt>
          <dd>{integer(usage.classesDesigned)}</dd>
        </div>
        <div>
          <dt>{t('usage.revisions')}</dt>
          <dd>{integer(usage.revisions)}</dd>
        </div>
      </dl>
      <p className="muted">
        {reused > 0
          ? t('usage.tokensExplainedReused', {
              input: integer(total.inputTokens),
              output: integer(total.outputTokens),
              reused: integer(reused),
            })
          : t('usage.tokensExplained', {
              input: integer(total.inputTokens),
              output: integer(total.outputTokens),
            })}
      </p>
      {usage.byKindModel.length > 0 ? (
        <table className="usage-table">
          <caption className="muted">{t('usage.byKind')}</caption>
          <thead>
            <tr>
              <th scope="col">{t('usage.col.work')}</th>
              <th scope="col">{t('usage.col.model')}</th>
              <th scope="col">{t('usage.col.tokens')}</th>
              <th scope="col">{t('usage.col.cost')}</th>
            </tr>
          </thead>
          <tbody>
            {usage.byKindModel.map((row) => (
              <tr key={`${row.jobKind}/${row.model}`}>
                <td>{Object.hasOwn(KINDS, row.jobKind) ? t(KINDS[row.jobKind]!) : row.jobKind}</td>
                <td>{row.model}</td>
                <td>
                  {integer(row.inputTokens + row.outputTokens + row.cacheReadTokens + row.cacheWriteTokens)}
                </td>
                <td>{dollars(row.costMicros)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <h3>{t('usage.leavesOut')}</h3>
      <p>{t('usage.lowerThanBill')}</p>
      <ul data-testid="usage-notes">
        {usage.notes.map((code) => (
          <li key={code}>{Object.hasOwn(NOTES, code) ? t(NOTES[code]!) : t('usage.note.other')}</li>
        ))}
      </ul>
    </Section>
  )
}
