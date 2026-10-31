import { lengthLabel, profileSeconds } from '../../lib/classCards'

/**
 * A small class profile on the stage (ADR 0013 sections 2 and 7): x is time, bar height and colour
 * are the zone. A sketch only: the full chart with axes and markers is the class page's.
 */
export function MiniProfile({ profile, tall = false }: { profile: [number, number][]; tall?: boolean }) {
  const total = profileSeconds(profile)
  const bars = profile.filter(([, sec]) => sec > 0)
  const label =
    total > 0 ? `Class profile, ${lengthLabel(total)}, ${String(bars.length)} intervals` : 'No class profile yet'
  const starts = bars.map((_, i) => bars.slice(0, i).reduce((sum, [, sec]) => sum + sec, 0))
  return (
    <svg
      role="img"
      aria-label={label}
      viewBox="0 0 200 56"
      preserveAspectRatio="none"
      className={`block w-full rounded-md bg-stage ${tall ? 'h-40' : 'h-14'}`}
    >
      {bars.map(([zone, sec], i) => {
        const z = Math.min(Math.max(zone, 1), 7)
        const height = 6 + z * 6.5
        return (
          <rect
            key={i}
            x={((starts[i] ?? 0) / total) * 200}
            y={56 - height}
            width={Math.max((sec / total) * 200 - 0.5, 0.5)}
            height={height}
            style={{ fill: `var(--cb-zone-${String(z)})` }}
          />
        )
      })}
    </svg>
  )
}
