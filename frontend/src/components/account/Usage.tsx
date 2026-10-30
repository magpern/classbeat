import { useEffect, useState } from 'react'
import { getUsage, type Usage as UsageData } from '../../api/usage'
import { Notice, Section } from '../forms'

// What the figure leaves out, as the backend's `notes` codes (ADR 0007 section 10).
const NOTES: Record<string, string> = {
  estimate_at_list_prices:
    'The cost is an estimate at the provider’s list prices. It is not an invoice.',
  sdk_retries:
    'The software that talks to the provider retries quietly inside a request. Those extra attempts are not counted.',
  lost_lease_turn:
    'A step that finished after classbeat had already given its work to another worker cannot be recorded.',
  failed_stream_turn:
    'A request that failed after the provider began to bill it returns no usage, so it is not counted.',
}

const OTHER_NOTE = 'There is another reason this figure may differ from the provider’s bill.'

const KINDS: Record<string, string> = {
  design_draft: 'Designing a class',
  revise_draft: 'Changing a class',
}

export function dollars(micros: number): string {
  if (micros <= 0) return '$0.00'
  if (micros < 10_000) return 'under $0.01'
  return `$${(micros / 1_000_000).toFixed(2)}`
}

const number = new Intl.NumberFormat('en-US')

function monthName(month: string): string {
  const match = /^(\d{4})-(\d{2})$/.exec(month)
  if (match === null) return month
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, 1))
  return date.toLocaleString('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' })
}

/**
 * Plan and usage (roadmap section 8, ADR 0007 section 10): this month's tokens, estimated cost,
 * classes designed and revisions, and what the figure leaves out. Months are UTC months.
 */
export function Usage() {
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
      <Section title="Usage">
        {failed ? (
          <>
            <Notice kind="error">Your usage could not be loaded.</Notice>
            <button
              type="button"
              className="button"
              onClick={() => {
                setRound((n) => n + 1)
              }}
            >
              Try again
            </button>
          </>
        ) : (
          <p>Loading</p>
        )}
      </Section>
    )
  }
  const { total } = usage
  const tokens = total.inputTokens + total.outputTokens + total.cacheReadTokens + total.cacheWriteTokens
  return (
    <Section title="Usage">
      <p>Your use in {monthName(usage.month)} (UTC months).</p>
      <dl className="facts" data-testid="usage-facts">
        <div>
          <dt>Tokens</dt>
          <dd>{number.format(tokens)}</dd>
        </div>
        <div>
          <dt>Estimated cost</dt>
          <dd>{dollars(total.costMicros)}</dd>
        </div>
        <div>
          <dt>Classes designed</dt>
          <dd>{number.format(usage.classesDesigned)}</dd>
        </div>
        <div>
          <dt>Revisions</dt>
          <dd>{number.format(usage.revisions)}</dd>
        </div>
      </dl>
      <p className="muted">
        Tokens are the pieces of text the designer reads and writes: {number.format(total.inputTokens)}{' '}
        in, {number.format(total.outputTokens)} out
        {total.cacheReadTokens + total.cacheWriteTokens > 0
          ? `, and ${number.format(total.cacheReadTokens + total.cacheWriteTokens)} reused from earlier requests`
          : ''}
        . Saving to MOWL uses none.
      </p>
      {usage.byKindModel.length > 0 ? (
        <table className="usage-table">
          <caption className="muted">By kind of work and model</caption>
          <thead>
            <tr>
              <th scope="col">Work</th>
              <th scope="col">Model</th>
              <th scope="col">Tokens</th>
              <th scope="col">Cost</th>
            </tr>
          </thead>
          <tbody>
            {usage.byKindModel.map((row) => (
              <tr key={`${row.jobKind}/${row.model}`}>
                <td>{Object.hasOwn(KINDS, row.jobKind) ? KINDS[row.jobKind] : row.jobKind}</td>
                <td>{row.model}</td>
                <td>
                  {number.format(
                    row.inputTokens + row.outputTokens + row.cacheReadTokens + row.cacheWriteTokens,
                  )}
                </td>
                <td>{dollars(row.costMicros)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <h3>What this leaves out</h3>
      <p>The figure may be slightly lower than your provider&rsquo;s bill.</p>
      <ul data-testid="usage-notes">
        {usage.notes.map((code) => (
          <li key={code}>{Object.hasOwn(NOTES, code) ? NOTES[code] : OTHER_NOTE}</li>
        ))}
      </ul>
    </Section>
  )
}
