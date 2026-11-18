import { useT } from '../../i18n/I18n'
import type { CatalogueKey } from '../../i18n/en'

/** The strip of figures over the list: classes, saved to MOWL and (when the plan is known) left this month. */
export function ClassesStats({ total, saved, left }: { total: number; saved: number; left: number | null }) {
  const t = useT()
  const tiles: { label: CatalogueKey; value: number }[] = [
    { label: 'class.list.statClasses', value: total },
    { label: 'class.list.statSaved', value: saved },
    ...(left === null ? [] : [{ label: 'class.list.statLeft' as const, value: left }]),
  ]
  return (
    <dl aria-label={t('class.list.stats')} className="m-0 grid grid-cols-[repeat(auto-fit,minmax(9rem,1fr))] gap-3">
      {tiles.map((tile) => (
        <div key={tile.label} className="flex flex-col gap-1.5 rounded-lg border border-line bg-surface px-5 py-4">
          <dt className="text-xs font-semibold tracking-[0.14em] text-muted uppercase">{t(tile.label)}</dt>
          <dd className="m-0 font-display text-3xl font-extrabold tabular-nums">{String(tile.value)}</dd>
        </div>
      ))}
    </dl>
  )
}
