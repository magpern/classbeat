import { Search } from 'lucide-react'
import { useId, useState } from 'react'
import { ClassList, ClassRow } from '../components/classes/ClassRow'
import { EmptyPicture } from '../components/imagery/EmptyPicture'
import { Notice } from '../components/forms'
import { RequireAuth } from '../components/RequireAuth'
import { Button } from '../components/ui/button'
import { Card, CardContent, CardTitle } from '../components/ui/card'
import { ChipButton } from '../components/ui/chip'
import { useClassList } from '../hooks/useClassList'
import { matchesFilter, type ClassFilter } from '../lib/classCards'
import { DuplicateClass } from '../components/classes/DuplicateClass'
import { Link } from '../router/Router'
import { useT } from '../i18n/I18n'
import type { CatalogueKey } from '../i18n/en'

export function ClassesPage() {
  return (
    <RequireAuth>
      <Classes />
    </RequireAuth>
  )
}

const FILTERS: readonly { value: ClassFilter; label: CatalogueKey }[] = [
  { value: 'all', label: 'class.list.filterAll' },
  { value: 'saved', label: 'class.list.filterSaved' },
  { value: 'drafts', label: 'class.list.filterDrafts' },
]

function Classes() {
  const t = useT()
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
        <h1 className="m-0 font-display text-3xl font-extrabold">{t('class.list.title')}</h1>
        <Button asChild>
          <Link to="/classes/new">{t('class.list.design')}</Link>
        </Button>
      </div>
      {listing.status === 'loading' ? <p className="m-0 text-muted">{t('class.list.loading')}</p> : null}
      {listing.status === 'failed' ? (
        <>
          <Notice kind="error">{t('class.list.failed')}</Notice>
          <p className="m-0">
            <Button variant="secondary" onClick={retry}>
              {t('class.tryAgain')}
            </Button>
          </p>
        </>
      ) : null}
      {listing.status === 'ready' && listing.classes.length === 0 ? (
        <Card role="region" aria-labelledby="none-title">
          <CardTitle id="none-title">{t('class.list.none')}</CardTitle>
          <EmptyPicture slot="emptyClasses">
            <CardContent>{t('class.list.noneText')}</CardContent>
          </EmptyPicture>
        </Card>
      ) : null}
      {listing.status === 'ready' && listing.classes.length > 0 ? (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <div role="group" aria-label={t('class.list.show')} className="flex flex-wrap gap-2">
              {FILTERS.map((f) => (
                <ChipButton
                  key={f.value}
                  pressed={filter === f.value}
                  onClick={() => {
                    setFilter(f.value)
                  }}
                >
                  {t(f.label)}
                </ChipButton>
              ))}
            </div>
            <div className="relative ml-auto min-w-48 flex-1 sm:max-w-xs">
              <label htmlFor={searchId} className="sr-only">
                {t('class.list.search')}
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
                placeholder={t('class.list.search')}
                onChange={(e) => {
                  setQuery(e.target.value)
                }}
                className="min-h-11 w-full rounded-full border border-line bg-raised pr-4 pl-10 text-text"
              />
            </div>
          </div>
          {shown.length === 0 ? (
            <p className="m-0 text-muted">{t('class.list.noMatch')}</p>
          ) : (
            <ClassList>
              {shown.map((c) => (
                <ClassRow
                  key={c.id}
                  cls={c}
                  actions={
                    <DuplicateClass
                      classId={c.id}
                      title={c.title}
                      revisionId={c.currentRevisionId}
                      versionNumber={c.currentRevisionNumber}
                    />
                  }
                />
              ))}
            </ClassList>
          )}
        </>
      ) : null}
    </div>
  )
}
