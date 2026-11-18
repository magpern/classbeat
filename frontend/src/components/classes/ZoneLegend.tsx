import { useT } from '../../i18n/I18n'

/** The four zone bands of a class profile, named, in the zone colours (swatches are decoration). */
const BANDS = [
  { swatch: 'bg-zone-2', label: 'class.list.legendEndurance' },
  { swatch: 'bg-zone-3', label: 'class.list.legendTempo' },
  { swatch: 'bg-zone-4', label: 'class.list.legendThreshold' },
  { swatch: 'bg-zone-5', label: 'class.list.legendHard' },
] as const

export function ZoneLegend() {
  const t = useT()
  return (
    <ul
      aria-label={t('class.list.zones')}
      className="m-0 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-xs text-stage-muted"
    >
      {BANDS.map((band) => (
        <li key={band.label} className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className={`size-2.5 rounded-sm ${band.swatch}`} />
          {t(band.label)}
        </li>
      ))}
    </ul>
  )
}
