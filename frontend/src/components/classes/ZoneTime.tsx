import { useT } from '../../i18n/I18n'
import { zoneNames, type Bar, zoneCount, zoneSeconds } from '../../lib/profile'
import { clock } from '../../lib/workout'

/** "Time in each zone": one row per zone with its name, a bar scaled to the largest zone and the time. */
export function ZoneTime({ bars, running = false }: { bars: Bar[]; running?: boolean }) {
  const t = useT()
  const seconds = zoneSeconds(bars)
  const most = Math.max(...seconds, 1)
  return (
    <section aria-labelledby="zone-time-title" className="flex flex-col gap-2">
      <h3 id="zone-time-title" className="m-0 font-display text-lg font-bold">
        {t('class.zoneTime.title')}
      </h3>
      <ul className="m-0 flex list-none flex-col gap-1 p-0">
        {zoneNames().slice(0, zoneCount(running)).map((name, i) => (
          <li key={name} className="grid grid-cols-[9rem_1fr_3.5rem] items-center gap-3 text-[0.9375rem]">
            <span>{t('class.zone.line', { zone: i + 1, name })}</span>
            <span aria-hidden="true" className="h-3 rounded-full bg-raised">
              <span
                className="block h-3 rounded-full"
                style={{
                  width: `${String(((seconds[i] ?? 0) / most) * 100)}%`,
                  background: `var(--cb-zone-${String(i + 1)})`,
                }}
              />
            </span>
            <span className="text-right tabular-nums text-muted">{clock(seconds[i] ?? 0)}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}
