import { useEffect, useState } from 'react'
import { listClasses, type ClassSummary } from '../api/classes'
import { Card, Notice } from '../components/forms'
import { RequireAuth } from '../components/RequireAuth'
import { Link } from '../router/Router'

export function ClassesPage() {
  return (
    <RequireAuth>
      <Classes />
    </RequireAuth>
  )
}

type Listing =
  | { status: 'loading' }
  | { status: 'failed' }
  | { status: 'ready'; classes: ClassSummary[] }

function when(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString()
}

function status(c: ClassSummary): string {
  if (c.openJob?.kind === 'design_draft') return 'Being designed'
  if (c.openJob?.kind === 'revise_draft') return 'Being changed'
  if (c.openJob !== null) return 'Busy'
  if (c.currentRevisionNumber === null) return 'No version yet'
  const parts = [`Version ${String(c.currentRevisionNumber)} of ${String(c.revisionCount)}`]
  if (c.saved.count > 0) {
    parts.push(c.saved.latestUpToDate === false ? 'saved, with newer changes' : 'saved to MOWL')
  }
  return parts.join(', ')
}

function Classes() {
  const [listing, setListing] = useState<Listing>({ status: 'loading' })
  const [round, setRound] = useState(0)
  useEffect(() => {
    const abort = new AbortController()
    listClasses(abort.signal).then(
      (classes) => {
        if (!abort.signal.aborted) setListing({ status: 'ready', classes })
      },
      () => {
        if (!abort.signal.aborted) setListing({ status: 'failed' })
      },
    )
    return () => {
      abort.abort()
    }
  }, [round])

  return (
    <div className="stack">
      <h1>My classes</h1>
      {listing.status === 'loading' ? <p>Loading your classes</p> : null}
      {listing.status === 'failed' ? (
        <>
          <Notice kind="error">Your classes could not be loaded.</Notice>
          <p>
            <button
              type="button"
              className="button"
              onClick={() => {
                setListing({ status: 'loading' })
                setRound((n) => n + 1)
              }}
            >
              Try again
            </button>
          </p>
        </>
      ) : null}
      {listing.status === 'ready' && listing.classes.length === 0 ? (
        <Card title="No classes yet">
          <p>Describe a class and classbeat designs it from your playlist.</p>
          <p>
            <Link className="button" to="/classes/new">
              Design a class
            </Link>
          </p>
        </Card>
      ) : null}
      {listing.status === 'ready' && listing.classes.length > 0 ? (
        <>
          <ul className="rows class-list">
            {listing.classes.map((c) => (
              <li key={c.id}>
                <span>
                  <Link to={`/classes/${c.id}`}>{c.title}</Link>
                  <br />
                  <span className="muted">
                    {status(c)}
                    {when(c.updatedAt) ? `, updated ${when(c.updatedAt)}` : ''}
                  </span>
                </span>
              </li>
            ))}
          </ul>
          <p>
            <Link className="button" to="/classes/new">
              Design another class
            </Link>
          </p>
        </>
      ) : null}
    </div>
  )
}
