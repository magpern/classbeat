import { Search } from 'lucide-react'
import { useId } from 'react'
import { useT } from '../../i18n/I18n'
import type { CatalogueKey } from '../../i18n/en'
import type { ClassFilter } from '../../lib/classCards'
import { SegmentedControl } from '../ui/segmented-control'

const FILTERS: readonly { value: ClassFilter; label: CatalogueKey }[] = [
  { value: 'all', label: 'class.list.filterAll' },
  { value: 'saved', label: 'class.list.filterSaved' },
  { value: 'drafts', label: 'class.list.filterDrafts' },
]

/** The filter as a pill track with counts, and the search as a pill. Filtering is client-side. */
export function ClassesFilters({
  filter,
  onFilter,
  counts,
  query,
  onQuery,
}: {
  filter: ClassFilter
  onFilter: (filter: ClassFilter) => void
  counts: Record<ClassFilter, number>
  query: string
  onQuery: (query: string) => void
}) {
  const t = useT()
  const searchId = useId()
  return (
    <div className="flex flex-wrap items-center gap-3">
      <SegmentedControl
        aria-label={t('class.list.show')}
        value={filter}
        onValueChange={onFilter}
        options={FILTERS.map((f) => ({
          value: f.value,
          label: (
            <>
              {t(f.label)}
              <span className="ml-1.5 font-normal tabular-nums">{String(counts[f.value])}</span>
            </>
          ),
        }))}
      />
      <div className="relative ml-auto min-w-48 flex-1 sm:max-w-sm">
        <label htmlFor={searchId} className="sr-only">
          {t('class.list.search')}
        </label>
        <Search
          aria-hidden="true"
          size={18}
          strokeWidth={2}
          className="pointer-events-none absolute top-1/2 left-4 -translate-y-1/2 text-muted"
        />
        <input
          id={searchId}
          type="search"
          value={query}
          placeholder={t('class.list.search')}
          onChange={(e) => {
            onQuery(e.target.value)
          }}
          className="min-h-12 w-full rounded-full border border-line bg-raised pr-4 pl-11 text-text"
        />
      </div>
    </div>
  )
}
