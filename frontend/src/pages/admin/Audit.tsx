import { useEffect, useState, type FormEvent } from 'react'
import { adminError } from '../../api/admin'
import {
  filterFromSearch,
  filterSearch,
  listAudit,
  type AuditFilter,
  type AuditRow,
} from '../../api/adminViews'
import { Field, Notice } from '../../components/forms'
import { SelectField } from '../../components/classes/fields'
import { Button } from '../../components/ui/button'
import { Chip } from '../../components/ui/chip'
import { Link, useRouter } from '../../router/Router'
import { isReason, useMfaRequired } from './StepUp'
import { DataTable, SectionTitle, when } from './Table'

const OUTCOMES = [
  { id: '', name: 'Any' },
  { id: 'ok', name: 'OK' },
  { id: 'refused', name: 'Refused' },
  { id: 'error', name: 'Error' },
] as const

function target(row: AuditRow) {
  const what = [row.targetKind, row.targetId].filter((part) => part !== null).join(' ')
  if (row.targetUserId === null) return what
  return (
    <>
      <Link to={`/admin/users/${row.targetUserId}`}>user</Link>
      {what !== '' ? ` ${what}` : ''}
    </>
  )
}

function outcome(row: AuditRow) {
  const tone = row.outcome === 'ok' ? undefined : 'bad'
  return (
    <>
      <Chip tone={tone}>{row.outcome}</Chip>
      {row.reason !== null ? ` ${row.reason}` : ''}
    </>
  )
}


const TEXT_FIELDS: { field: keyof AuditFilter; label: string; max: number; hint?: string }[] = [
  { field: 'action', label: 'Action', max: 60, hint: 'A code, or a prefix ending in a dot: user.' },
  { field: 'actorId', label: 'Admin id', max: 36 },
  { field: 'targetUserId', label: 'Target user id', max: 36 },
  { field: 'targetKind', label: 'Target kind', max: 40 },
  { field: 'targetId', label: 'Target id', max: 100 },
]

/**
 * The filter form. It is keyed by the applied filter, so a new address (a submit, Back or
 * Forward) starts it again from what the address says.
 */
function AuditFilters({
  initial,
  initialEmail,
  onApply,
  onClear,
}: {
  initial: AuditFilter
  initialEmail: string
  onApply: (filter: AuditFilter, email: string) => void
  onClear: () => void
}) {
  const [form, setForm] = useState<AuditFilter>(initial)
  const [email, setEmail] = useState(initialEmail)
  const set = (field: keyof AuditFilter, value: string) => {
    setForm((f) => ({ ...f, [field]: value }))
  }
  const apply = (event: FormEvent) => {
    event.preventDefault()
    onApply(form, email.trim())
  }
  return (
    <form onSubmit={apply} aria-label="Filter the audit log" className="mb-4 flex flex-wrap items-end gap-2">
      {TEXT_FIELDS.slice(0, 1).map(({ field, label, max, hint }) => (
        <Field key={field} label={label} hint={hint} maxLength={max} autoComplete="off" spellCheck={false}
          value={form[field]} onChange={(e) => { set(field, e.target.value) }} />
      ))}
      <SelectField label="Outcome" value={form.outcome} onChange={(e) => { set('outcome', e.target.value) }}>
        {OUTCOMES.map((o) => (
          <option key={o.id} value={o.id}>
            {o.name}
          </option>
        ))}
      </SelectField>
      <Field label="Admin email" hint="Not kept in the address." type="search" maxLength={320} autoComplete="off"
        spellCheck={false} value={email} onChange={(e) => { setEmail(e.target.value) }} />
      {TEXT_FIELDS.slice(1).map(({ field, label, max }) => (
        <Field key={field} label={label} maxLength={max} autoComplete="off" spellCheck={false}
          value={form[field]} onChange={(e) => { set(field, e.target.value) }} />
      ))}
      <Field label="From" type="date" value={form.since} onChange={(e) => { set('since', e.target.value) }} />
      <Field label="To (inclusive)" type="date" value={form.until} onChange={(e) => { set('until', e.target.value) }} />
      <Button type="submit">Filter</Button>
      <Button type="button" variant="secondary" onClick={onClear}>
        Clear
      </Button>
    </form>
  )
}

type Page = { rows: AuditRow[]; nextBefore: string | null }

/**
 * The audit log (ADR 0011 section 6), newest first. The filters live in the page's address
 * (`?action=user.&outcome=refused&since=2026-10-01`), so a filtered view can be reloaded or
 * shared with another admin; an actor email is the one filter kept out of the address and sent
 * only in the API request. Paging is the API's cursor, kept in this page's state.
 */
export function Audit() {
  const onMfaRequired = useMfaRequired()
  const { pathname, search, navigate } = useRouter()
  const applied = filterFromSearch(new URLSearchParams(search))
  const appliedKey = filterSearch(applied)
  // The email filter, for the address it was applied with: Back, Forward or another address
  // drops it (it is never in the address, so it cannot come back with one).
  const [appliedEmail, setAppliedEmail] = useState<{ key: string; email: string }>({ key: '', email: '' })
  const email = appliedEmail.key === appliedKey ? appliedEmail.email : ''
  // The cursors of the pages seen, for the filter and email they belong to: a new filter starts
  // at page 1.
  const pagingKey = `${appliedKey}\n${email}`
  const [paging, setPaging] = useState<{ key: string; cursors: (string | null)[] }>({ key: '', cursors: [null] })
  const cursors = paging.key === pagingKey ? paging.cursors : [null]
  const before = cursors[cursors.length - 1] ?? null
  // What was loaded, for the request it answers: another request shows "Loading" until its answer.
  const loadKey = `${pagingKey}\n${before ?? ''}`
  const [loaded, setLoaded] = useState<{ key: string; page: Page | null; error: string | null }>({
    key: '\u0000',
    page: null,
    error: null,
  })
  const page = loaded.key === loadKey ? loaded.page : null
  const error = loaded.key === loadKey ? loaded.error : null

  useEffect(() => {
    const controller = new AbortController()
    listAudit(filterFromSearch(new URLSearchParams(appliedKey)), email, before, controller.signal).then(
      (found) => {
        setLoaded({ key: loadKey, page: found, error: null })
      },
      (caught: unknown) => {
        if (controller.signal.aborted) return
        if (isReason(caught, 403, 'mfa_required')) onMfaRequired()
        else setLoaded({ key: loadKey, page: null, error: adminError(caught) })
      },
    )
    return () => {
      controller.abort()
    }
  }, [appliedKey, email, before, loadKey, onMfaRequired])

  return (
    <section aria-labelledby="admin-audit-title">
      <SectionTitle id="admin-audit-title">Audit log</SectionTitle>
      <AuditFilters
        key={appliedKey}
        initial={applied}
        initialEmail={email}
        onApply={(filter, typed) => {
          setAppliedEmail({ key: filterSearch(filter), email: typed })
          navigate(`${pathname}${filterSearch(filter)}`)
        }}
        onClear={() => {
          setAppliedEmail({ key: '', email: '' })
          navigate(pathname)
        }}
      />
      {error !== null ? <Notice kind="error">{error}</Notice> : null}
      {page === null && error === null ? <p>Loading</p> : null}
      {page !== null ? (
        <>
          <DataTable
            caption="Audit log entries"
            head={['When', 'Admin', 'Action', 'Target', 'Outcome', 'Details']}
            empty="No entries match."
            rows={page.rows.map((row) => ({
              key: row.id,
              cells: [
                when(row.createdAt),
                row.actorEmail,
                row.action,
                target(row),
                outcome(row),
                Object.keys(row.params).length > 0 ? <code className="break-all">{JSON.stringify(row.params)}</code> : '',
              ],
            }))}
          />
          <div className="mt-4 flex gap-2">
            {cursors.length > 1 ? (
              <Button
                variant="secondary"
                onClick={() => {
                  setPaging({ key: pagingKey, cursors: cursors.slice(0, -1) })
                }}
              >
                Newer
              </Button>
            ) : null}
            {page.nextBefore !== null ? (
              <Button
                variant="secondary"
                onClick={() => {
                  const next = page.nextBefore
                  if (next !== null) setPaging({ key: pagingKey, cursors: [...cursors, next] })
                }}
              >
                Older
              </Button>
            ) : null}
          </div>
        </>
      ) : null}
    </section>
  )
}
