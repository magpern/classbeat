import { useT } from '../../i18n/I18n'
import type { CatalogueKey } from '../../i18n/en'
import { StatTile, StatTiles } from '../ui/stat-tile'

/** The strip of figures over the list: classes, saved to MOWL and (when the plan is known) left this month. */
export function ClassesStats({ total, saved, left }: { total: number; saved: number; left: number | null }) {
  const t = useT()
  const tiles: { label: CatalogueKey; value: number }[] = [
    { label: 'class.list.statClasses', value: total },
    { label: 'class.list.statSaved', value: saved },
    ...(left === null ? [] : [{ label: 'class.list.statLeft' as const, value: left }]),
  ]
  return (
    <StatTiles aria-label={t('class.list.stats')}>
      {tiles.map((tile) => (
        <StatTile key={tile.label} label={t(tile.label)} value={String(tile.value)} />
      ))}
    </StatTiles>
  )
}
