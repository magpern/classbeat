import { useState } from 'react'
import { adminError, refundCredit, type LedgerRow } from '../../api/admin'
import { Notice, Section, useAction } from '../../components/forms'
import { Button } from '../../components/ui/button'
import { dayOf } from './format'
import { useAdminWrite } from './StepUp'

const CELL = 'border-b border-line py-2 pr-4'

const STATES: Record<string, string> = {
  reserved: 'running',
  committed: 'used',
  released: 'given back',
  refunded: 'refunded',
}

/**
 * The user's credits of the last three months (ADR 0011 section 6) and the admin refund of a
 * used (`committed`) one (#221): a step-up write, asked to be confirmed first. Only a used
 * credit offers it; the server refuses every other (409, shown in words).
 */
export function Credits({ userId, ledger, onChanged }: { userId: string; ledger: LedgerRow[]; onChanged: () => void }) {
  const write = useAdminWrite()
  const action = useAction()
  const [confirming, setConfirming] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  const refund = (ledgerId: string) => {
    setDone(false)
    const task = async () => {
      await refundCredit(userId, ledgerId)
      return true
    }
    void action.run(() => write(task), adminError).then((refunded) => {
      setConfirming(null)
      if (refunded === true) {
        setDone(true)
        onChanged()
      }
    })
  }

  return (
    <Section title="Credits, last three months">
      {action.error !== null ? <Notice kind="error">{action.error}</Notice> : null}
      {done ? <Notice kind="success">Refunded. The class no longer counts this month.</Notice> : null}
      {ledger.length === 0 ? (
        <p className="text-muted">No classes in these months.</p>
      ) : (
        <div className="relative overflow-x-auto" tabIndex={0} role="region" aria-label="Credits">
          <table className="w-full border-collapse text-left text-[0.9375rem]">
            <caption className="sr-only">Credits</caption>
            <thead className="text-muted">
              <tr>
                <th className={`${CELL} font-medium`}>Month</th>
                <th className={`${CELL} font-medium`}>Plan</th>
                <th className={`${CELL} font-medium`}>State</th>
                <th className={`${CELL} font-medium`}>Started</th>
                <th className={`${CELL} font-medium`}>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {ledger.map((row) => (
                <tr key={row.id}>
                  <td className={CELL}>{row.period.slice(0, 7)}</td>
                  <td className={CELL}>{row.planId}</td>
                  <td className={CELL}>
                    {STATES[row.state] ?? row.state}
                    {row.reason !== null ? ` (${row.reason.replace(/_/g, ' ')})` : ''}
                  </td>
                  <td className={CELL}>{dayOf(row.createdAt)}</td>
                  <td className={CELL}>
                    {row.state !== 'committed' ? null : confirming === row.id ? (
                      <span className="inline-flex flex-wrap items-center gap-2">
                        <Button
                          variant="danger"
                          disabled={action.busy}
                          onClick={() => {
                            refund(row.id)
                          }}
                        >
                          Confirm refund
                        </Button>
                        <Button
                          variant="ghost"
                          disabled={action.busy}
                          onClick={() => {
                            setConfirming(null)
                          }}
                        >
                          Cancel
                        </Button>
                      </span>
                    ) : (
                      <Button
                        variant="secondary"
                        disabled={action.busy}
                        onClick={() => {
                          action.clear()
                          setDone(false)
                          setConfirming(row.id)
                        }}
                      >
                        Refund
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  )
}
