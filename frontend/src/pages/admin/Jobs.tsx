import { useEffect, useState } from 'react'
import { adminError } from '../../api/admin'
import { getJobs, JOB_WINDOWS, type Jobs as JobsData } from '../../api/adminViews'
import { Notice } from '../../components/forms'
import { SelectField } from '../../components/classes/fields'
import { Link, useQuery, useRouter } from '../../router/Router'
import { isReason, useMfaRequired } from './StepUp'
import { DataTable, SectionTitle, SubTitle, when } from './Table'

function windowOf(value: string | null): number {
  const days = Number(value)
  return (JOB_WINDOWS as readonly number[]).includes(days) ? days : 7
}

function owner(userId: string, email: string | null) {
  return (
    <Link key="owner" to={`/admin/users/${userId}`}>
      {email ?? 'Deleted account'}
    </Link>
  )
}

/**
 * Jobs (ADR 0011 section 6), read only: counts per state, waiting approvals, recent failures by
 * category and uncertain external operations. An uncertain operation stays the user's to resolve
 * (ADR 0005 section 7), so there is no action here.
 */
export function Jobs() {
  const onMfaRequired = useMfaRequired()
  const { pathname, navigate } = useRouter()
  const days = windowOf(useQuery().get('days'))
  // What was loaded, for the window it belongs to: another window shows "Loading" until its answer.
  const [loaded, setLoaded] = useState<{ days: number; data: JobsData | null; error: string | null }>({
    days: 0,
    data: null,
    error: null,
  })
  const data = loaded.days === days ? loaded.data : null
  const error = loaded.days === days ? loaded.error : null

  useEffect(() => {
    const controller = new AbortController()
    getJobs(days, controller.signal).then(
      (found) => {
        setLoaded({ days, data: found, error: null })
      },
      (caught: unknown) => {
        if (controller.signal.aborted) return
        if (isReason(caught, 403, 'mfa_required')) onMfaRequired()
        else setLoaded({ days, data: null, error: adminError(caught) })
      },
    )
    return () => {
      controller.abort()
    }
  }, [days, onMfaRequired])

  return (
    <section aria-labelledby="admin-jobs-title">
      <SectionTitle id="admin-jobs-title">Jobs</SectionTitle>
      {error !== null ? <Notice kind="error">{error}</Notice> : null}
      {data === null && error === null ? <p>Loading</p> : null}
      {data !== null ? (
        <>
          <SubTitle>Jobs by state</SubTitle>
          <DataTable
            caption="Jobs by state"
            head={['State', 'Jobs']}
            empty="No jobs."
            rows={data.states.map((s) => ({ key: s.state, cells: [s.state, s.count] }))}
          />

          <SubTitle>Waiting for approval</SubTitle>
          <DataTable
            caption="Jobs waiting for approval"
            head={['User', 'Kind', 'Waiting since', 'Expires']}
            empty="Nothing is waiting for approval."
            rows={data.waitingApprovals.map((w, index) => ({
              key: `${w.userId}-${String(index)}`,
              cells: [owner(w.userId, w.email), w.kind, when(w.waitingSince), when(w.expiresAt)],
            }))}
          />

          <SubTitle>Uncertain external operations</SubTitle>
          <p className="m-0 mb-2 text-muted">
            A write whose outcome is not known. The user resolves it from their class page.
          </p>
          <DataTable
            caption="Uncertain external operations"
            head={['User', 'Kind', 'Since', 'Job']}
            empty="No uncertain operations."
            rows={data.uncertainOperations.map((o, index) => ({
              key: `${o.userId}-${String(index)}`,
              cells: [owner(o.userId, o.email), o.kind, when(o.since), o.jobState ?? 'Class deleted'],
            }))}
          />

          <SubTitle>Recent failures</SubTitle>
          <form className="mb-2 flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault() }}>
            <SelectField
              label="Failures in the last"
              value={String(days)}
              onChange={(e) => {
                navigate(`${pathname}?days=${e.target.value}`)
              }}
            >
              {JOB_WINDOWS.map((d) => (
                <option key={d} value={String(d)}>
                  {d} days
                </option>
              ))}
            </SelectField>
          </form>
          <DataTable
            caption="Failed jobs by kind and category"
            head={['Kind', 'Category', 'Failures', 'Latest']}
            empty="No failures in this period."
            rows={data.failures.map((f) => ({
              key: `${f.kind}/${f.category}`,
              cells: [f.kind, f.category, f.count, when(f.lastAt)],
            }))}
          />
        </>
      ) : null}
    </section>
  )
}
