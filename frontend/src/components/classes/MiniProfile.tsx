import { useT } from '../../i18n/I18n'
import { cn } from '../../lib/cn'
import { zoneFill } from './zoneClasses'
import { lengthLabel, profileSeconds } from '../../lib/classCards'

/**
 * A small class profile on the stage (ADR 0013 sections 2 and 7): x is time, bar height and colour
 * are the zone. A sketch only: the full chart with axes and markers is the class page's.
 */
export function MiniProfile({
  profile,
  tall = false,
  motion,
}: {
  profile: [number, number][]
  tall?: boolean
  /**
   * How the profile moves (motion.css, CSS only): `play` draws it in on scroll and runs a playhead
   * across it all the time; `hover` runs the playhead while the pointer is on its card or row.
   */
  motion?: 'play' | 'hover'
}) {
  const t = useT() // renders again when the language changes: the length is looked up on each render
  const total = profileSeconds(profile)
  const bars = profile.filter(([, sec]) => sec > 0)
  const label =
    total > 0
      ? t('class.profile.label', { length: lengthLabel(total), count: bars.length })
      : t('class.profile.empty')
  const starts = bars.map((_, i) => bars.slice(0, i).reduce((sum, [, sec]) => sum + sec, 0))
  if (total <= 0) {
    return (
      <div
        role="img"
        aria-label={label}
        className={`w-full rounded-md border border-dashed border-line ${tall ? 'h-40' : 'h-14'}`}
      />
    )
  }
  return (
    <svg
      role="img"
      aria-label={label}
      viewBox="0 0 200 56"
      preserveAspectRatio="none"
      className={cn(
        'block w-full rounded-md bg-stage',
        tall ? 'h-40' : 'h-14',
        motion === 'play' && 'mini-profile--play draw-in',
        motion === 'hover' && 'mini-profile--hover',
      )}
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
            className={zoneFill(z)}
          />
        )
      })}
      {motion ? (
        <>
          {/* a path, not a rect: every rect of a profile is a bar */}
          <path className="mini-veil" d="M0 0H200V56H0Z" />
          <line className="mini-playhead" x1="0" x2="0" y1="0" y2="56" />
        </>
      ) : null}
    </svg>
  )
}
