import { ChevronDown } from 'lucide-react'
import { useId, useState } from 'react'
import { useT } from '../../i18n/I18n'
import { cn } from '../../lib/cn'
import { Button } from '../ui/button'
import { searchRows, type MusicRow } from './musicSources'

/**
 * "Music ▾": a closed disclosure with a search box over a list of source-labelled rows, so no
 * playlist grid is drawn on first load. Choosing the chosen row again goes back to "classbeat
 * chooses from my request". The destination (where the class is saved) is not a row: the screen
 * shows it on its own line.
 */
export function MusicSourcePicker({
  rows,
  value,
  onChange,
}: {
  rows: MusicRow[]
  value: string | null
  onChange: (key: string | null) => void
}) {
  const t = useT()
  const id = useId()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const chosen = rows.find((r) => r.key === value)
  const shown = searchRows(rows, query)
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <Button
        variant="secondary"
        aria-expanded={open}
        aria-controls={id}
        className="self-start"
        onClick={() => {
          setOpen((o) => !o)
        }}
      >
        {chosen ? t('start.music.chosen', { name: chosen.title }) : t('start.music.button')}
        <ChevronDown aria-hidden="true" size={16} className={cn('transition-transform', open && 'rotate-180')} />
      </Button>
      <div id={id} hidden={!open} className="flex flex-col gap-2">
        <input
          type="search"
          aria-label={t('start.music.search')}
          placeholder={t('start.music.search')}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
          }}
          className="min-h-11 w-full rounded-md border border-line bg-raised px-3 text-base text-text placeholder:text-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
        />
        <div role="radiogroup" aria-label={t('start.music.listLabel')} className="flex max-h-72 flex-col gap-1 overflow-y-auto">
          <MusicOption checked={value === null} title={t('start.music.auto')} source={t('start.music.autoHint')} onSelect={() => { onChange(null) }} />
          {shown.map((row) => (
            <MusicOption
              key={row.key}
              checked={row.key === value}
              title={row.title}
              source={row.source}
              onSelect={() => {
                onChange(row.key)
              }}
            />
          ))}
          {shown.length === 0 ? <p className="m-0 px-3 py-2 text-muted">{t('start.music.noMatch')}</p> : null}
        </div>
      </div>
    </div>
  )
}

function MusicOption({ checked, title, source, onSelect }: { checked: boolean; title: string; source: string; onSelect: () => void }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={checked}
      onClick={onSelect}
      className={cn(
        'flex min-h-11 w-full cursor-pointer flex-col items-start rounded-md border px-3 py-2 text-left',
        checked ? 'border-brand bg-raised' : 'border-line bg-transparent hover:bg-raised',
      )}
    >
      <span className="font-medium text-text">{title}</span>
      <span className="text-[0.8125rem] text-muted">{source}</span>
    </button>
  )
}
