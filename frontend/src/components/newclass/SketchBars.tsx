import { MAX_LEVEL } from '../../lib/sketch'

/**
 * The sketch on the stage: bars whose height is a relative level, all in one neutral colour. No
 * zone colour, number or name, because the sketch does not know the zones (see lib/sketch.ts).
 */
export function SketchBars({ profile }: { profile: [number, number][] }) {
  const total = profile.reduce((sum, [, sec]) => sum + sec, 0)
  let at = 0
  return (
    <svg
      role="img"
      aria-label={total > 0 ? 'A rough sketch of your class' : 'No sketch yet'}
      viewBox="0 0 200 56"
      preserveAspectRatio="none"
      className="block h-14 w-full rounded-md bg-stage"
    >
      {total > 0
        ? profile.map(([level, sec], i) => {
            const x = (at / total) * 200
            at += sec
            const height = 6 + (level / MAX_LEVEL) * 44
            return (
              <rect
                key={i}
                x={x}
                y={56 - height}
                width={Math.max((sec / total) * 200 - 0.5, 0.5)}
                height={height}
                style={{ fill: 'var(--cb-stage-muted)' }}
              />
            )
          })
        : null}
    </svg>
  )
}
