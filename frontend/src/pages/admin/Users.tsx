import { useEffect, useState, type FormEvent } from 'react'
import { adminError, dollars, listUsers, PAGE_SIZE, type UserPage } from '../../api/admin'
import { Field, Notice } from '../../components/forms'
import { Button } from '../../components/ui/button'
import { Chip } from '../../components/ui/chip'
import { Link } from '../../router/Router'
import { dayOf, sourceLabel } from './format'
import { isReason, useMfaRequired } from './StepUp'

/**
 * The Users list: search by email prefix and page through. The prefix is kept in this page's
 * state and sent only in the API request, never put in the page's address or stored.
 */
export function Users() {
  const onMfaRequired = useMfaRequired()
  const [typed, setTyped] = useState('')
  const [query, setQuery] = useState({ prefix: '', offset: 0 })
  const [page, setPage] = useState<UserPage | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    listUsers(query.prefix, query.offset, controller.signal).then(
      (found) => {
        setError(null)
        setPage(found)
      },
      (caught: unknown) => {
        if (controller.signal.aborted) return
        if (isReason(caught, 403, 'mfa_required')) onMfaRequired()
        else setError(adminError(caught))
      },
    )
    return () => {
      controller.abort()
    }
  }, [query, onMfaRequired])

  const search = (event: FormEvent) => {
    event.preventDefault()
    setQuery({ prefix: typed.trim(), offset: 0 })
  }

  return (
    <section aria-labelledby="admin-users-title">
      <h2 id="admin-users-title" className="m-0 mb-4 font-display text-xl font-bold">
        Users
      </h2>
      <form onSubmit={search} className="mb-4 flex flex-wrap items-end gap-2">
        <Field
          label="Email starts with"
          type="search"
          autoComplete="off"
          spellCheck={false}
          maxLength={254}
          value={typed}
          onChange={(e) => {
            setTyped(e.target.value)
          }}
        />
        <Button type="submit">Search</Button>
      </form>
      {error !== null ? <Notice kind="error">{error}</Notice> : null}
      {page === null && error === null ? <p>Loading</p> : null}
      {page !== null && page.users.length === 0 ? <p className="text-muted">No users found.</p> : null}
      {page !== null && page.users.length > 0 ? (
        <div className="relative overflow-x-auto" tabIndex={0} role="region" aria-label="Users">
          <table className="w-full border-collapse text-left text-[0.9375rem]">
            <thead className="text-muted">
              <tr>
                <th className="border-b border-line py-2 pr-4 font-medium">Email</th>
                <th className="border-b border-line py-2 pr-4 font-medium">Plan</th>
                <th className="border-b border-line py-2 pr-4 font-medium">Classes this month</th>
                <th className="border-b border-line py-2 pr-4 font-medium">Cost this month</th>
                <th className="border-b border-line py-2 pr-4 font-medium">Joined</th>
              </tr>
            </thead>
            <tbody>
              {page.users.map((user) => (
                <tr key={user.id}>
                  <td className="border-b border-line py-2 pr-4">
                    <Link to={`/admin/users/${user.id}`}>{user.email}</Link>{' '}
                    {user.role === 'admin' ? <Chip>admin</Chip> : null}{' '}
                    {user.disabledAt !== null ? <Chip tone="bad">disabled</Chip> : null}
                  </td>
                  <td className="border-b border-line py-2 pr-4">
                    {user.plan.name}
                    {sourceLabel(user.plan.source)}
                    {user.plan.endsAt !== null ? `, until ${dayOf(user.plan.endsAt)}` : ''}
                  </td>
                  <td className="border-b border-line py-2 pr-4">
                    {user.classesHeld} of {user.classesLimit}
                  </td>
                  <td className="border-b border-line py-2 pr-4">{dollars(user.costMicros)}</td>
                  <td className="border-b border-line py-2 pr-4">{dayOf(user.createdAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {page !== null ? (
        <div className="mt-4 flex gap-2">
          {query.offset > 0 ? (
            <Button
              variant="secondary"
              onClick={() => {
                setQuery((q) => ({ ...q, offset: Math.max(0, q.offset - PAGE_SIZE) }))
              }}
            >
              Previous
            </Button>
          ) : null}
          {page.nextOffset !== null ? (
            <Button
              variant="secondary"
              onClick={() => {
                const next = page.nextOffset
                if (next !== null) setQuery((q) => ({ ...q, offset: next }))
              }}
            >
              Next
            </Button>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
