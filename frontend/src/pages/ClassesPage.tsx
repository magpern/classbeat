import { Plus } from 'lucide-react'
import { useState } from 'react'
import type { ClassSummary } from '../api/classes'
import { ClassCard } from '../components/classes/ClassCard'
import { ClassFeature } from '../components/classes/ClassFeature'
import { ClassesFilters } from '../components/classes/ClassesFilters'
import { ClassesStats } from '../components/classes/ClassesStats'
import { DeleteClassFromList } from '../components/classes/DeleteClassFromList'
import { DuplicateClass } from '../components/classes/DuplicateClass'
import { EmptyPicture } from '../components/imagery/EmptyPicture'
import { Notice } from '../components/forms'
import { usePlanAllowance } from '../components/newclass/usePlanAllowance'
import { RequireAuth } from '../components/RequireAuth'
import { Button } from '../components/ui/button'
import { Card, CardContent, CardTitle } from '../components/ui/card'
import { PageHeading } from '../components/ui/page-heading'
import { useClassList } from '../hooks/useClassList'
import { matchesFilter, type ClassFilter } from '../lib/classCards'
import { classesStats, featureClass } from '../lib/classesView'
import { Link } from '../router/Router'
import { useT } from '../i18n/I18n'

export function ClassesPage() {
  return (
    <RequireAuth>
      <Classes />
    </RequireAuth>
  )
}

function Classes() {
  const t = useT()
  const { listing, retry } = useClassList()
  const { classesLeft } = usePlanAllowance()
  const [filter, setFilter] = useState<ClassFilter>('all')
  const [query, setQuery] = useState('')

  const all = listing.status === 'ready' ? listing.classes : []
  const needle = query.trim().toLowerCase()
  const shown = all.filter((c) => matchesFilter(c, filter) && c.title.toLowerCase().includes(needle))
  const feature = featureClass(shown)
  const rest = shown.filter((c) => c !== feature)
  const stats = classesStats(all)
  const counts: Record<ClassFilter, number> = {
    all: all.length,
    saved: stats.saved,
    drafts: all.filter((c) => matchesFilter(c, 'drafts')).length,
  }
  const actions = (c: ClassSummary) => (
    <>
      <DuplicateClass
        classId={c.id}
        title={c.title}
        revisionId={c.currentRevisionId}
        versionNumber={c.currentRevisionNumber}
      />
      <DeleteClassFromList summary={c} onDeleted={retry} />
    </>
  )

  return (
    <div className="flex flex-col gap-7">
      <PageHeading
        eyebrow={t('class.list.eyebrow')}
        title={t('class.list.title')}
        titleClassName="leading-none"
        actions={
          <Button asChild className="min-h-13 px-7 text-base font-bold">
            <Link to="/classes/new">
              <Plus aria-hidden="true" size={18} strokeWidth={2.5} />
              {t('class.list.design')}
            </Link>
          </Button>
        }
      />
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
          <ClassesStats total={stats.total} saved={stats.saved} left={classesLeft} />
          <ClassesFilters filter={filter} onFilter={setFilter} counts={counts} query={query} onQuery={setQuery} />
          {shown.length === 0 ? <p className="m-0 text-muted">{t('class.list.noMatch')}</p> : null}
          {feature ? <ClassFeature cls={feature} actions={actions(feature)} /> : null}
          {rest.length > 0 ? (
            <section aria-labelledby="more-classes" className="flex flex-col gap-4">
              {feature ? (
                <PageHeading as="h2" size="section" titleId="more-classes" title={t('class.list.othersTitle')} />
              ) : (
                <h2 id="more-classes" className="sr-only">
                  {t('class.list.othersTitle')}
                </h2>
              )}
              <ul className="m-0 grid list-none grid-cols-1 gap-4 p-0 sm:grid-cols-[repeat(auto-fill,minmax(20rem,1fr))]">
                {rest.map((c) => (
                  <ClassCard key={c.id} cls={c} actions={actions(c)} />
                ))}
              </ul>
            </section>
          ) : null}
        </>
      ) : null}
    </div>
  )
}
