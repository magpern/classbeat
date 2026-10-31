import { Search } from 'lucide-react'
import { useId, useState } from 'react'
import { ClassCard } from '../components/classes/ClassCard'
import { Notice } from '../components/forms'
import { RequireAuth } from '../components/RequireAuth'
import { Button } from '../components/ui/button'
import { Card, CardContent, CardTitle } from '../components/ui/card'
import { ChipButton } from '../components/ui/chip'
import { useClassList } from '../hooks/useClassList'
import { matchesFilter, type ClassFilter } from '../lib/classCards'
import { Link } from '../router/Router'

export function ClassesPage() {
  return (
    <RequireAuth>
      <Classes />
    </RequireAuth>
  )
}

const FILTERS: readonly { value: ClassFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'saved', label: 'Saved to MOWL' },
  { value: 'drafts', label: 'Drafts' },
]

function Classes() {
  const { listing, retry } = useClassList()
  const [filter, setFilter] = useState<ClassFilter>('all')
  const [query, setQuery] = useState('')
  const searchId = useId()

  const needle = query.trim().toLowerCase()
  const shown =
    listing.status === 'ready'
      ? listing.classes.filter((c) => matchesFilter(c, filter) && c.title.toLowerCase().includes(needle))
      : []

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="m-0 font-display text-3xl font-extrabold">My classes</h1>
        <Button asChild>
          <Link to="/classes/new">Design a class</Link>
        </Button>
      </div>
      {listing.status === 'loading' ? <p className="m-0 text-muted">Loading your classes</p> : null}
      {listing.status === 'failed' ? (
        <>
          <Notice kind="error">Your classes could not be loaded.</Notice>
          <p className="m-0">
            <Button variant="secondary" onClick={retry}>
              Try again
            </Button>
          </p>
        </>
      ) : null}
      {listing.status === 'ready' && listing.classes.length === 0 ? (
        <Card role="region" aria-labelledby="none-title">
          <CardTitle id="none-title">No classes yet</CardTitle>
          <CardContent>Describe a class and classbeat designs it from your playlist.</CardContent>
        </Card>
      ) : null}
      {listing.status === 'ready' && listing.classes.length > 0 ? (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <div role="group" aria-label="Show" className="flex flex-wrap gap-2">
              {FILTERS.map((f) => (
                <ChipButton
                  key={f.value}
                  pressed={filter === f.value}
                  onClick={() => {
                    setFilter(f.value)
                  }}
                >
                  {f.label}
                </ChipButton>
              ))}
            </div>
            <div className="relative ml-auto min-w-48 flex-1 sm:max-w-xs">
              <label htmlFor={searchId} className="sr-only">
                Search your classes
              </label>
              <Search
                aria-hidden="true"
                size={18}
                strokeWidth={2}
                className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted"
              />
              <input
                id={searchId}
                type="search"
                value={query}
                placeholder="Search your classes"
                onChange={(e) => {
                  setQuery(e.target.value)
                }}
                className="min-h-11 w-full rounded-full border border-line bg-raised pr-4 pl-10 text-text"
              />
            </div>
          </div>
          {shown.length === 0 ? (
            <p className="m-0 text-muted">No class matches. Change the filter or the search.</p>
          ) : (
            <ul className="m-0 grid list-none grid-cols-1 gap-4 p-0 sm:grid-cols-2 xl:grid-cols-3">
              {shown.map((c) => (
                <li key={c.id}>
                  <ClassCard cls={c} />
                </li>
              ))}
            </ul>
          )}
        </>
      ) : null}
    </div>
  )
}
