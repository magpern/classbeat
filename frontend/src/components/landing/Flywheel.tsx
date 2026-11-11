import { useId } from 'react'

const BARS = 96
const CENTRE = 150
/** Radius of the ring the marker runs on; the bars start just outside it. */
const RING = 60
const BAR_FROM = 66
const BAR_LENGTH = 34
/** The marker's fading tail: this many short arcs of TAIL_STEP degrees behind the dot. */
const TAIL = 10
const TAIL_STEP = 3

/** A coordinate for an SVG attribute: two decimals are finer than a pixel at any size we draw. */
const short = (value: number): string => String(Math.round(value * 100) / 100)

const onRing = (degrees: number): string => {
  const angle = (degrees * Math.PI) / 180
  return `${short(CENTRE + Math.cos(angle) * RING)} ${short(CENTRE + Math.sin(angle) * RING)}`
}

/**
 * The hero's moving backdrop: a flywheel whose bars kick on the beat and climb through the effort
 * zones, with a marker orbiting like a pedal. Decorative, behind the scrim.
 *
 * All of its motion is CSS (flywheel.css): the markup is the same in the prerendered page and in
 * the live one, so the takeover changes nothing, a visitor without script gets it too, and under
 * reduced motion it is a still picture. No `style`: the served pages' CSP refuses it.
 */
export function Flywheel() {
  const glow = useId()
  return (
    <svg className="flywheel" viewBox="0 0 300 300" aria-hidden="true" focusable="false">
      <defs>
        <radialGradient id={glow}>
          <stop offset="0.1" stopOpacity="0.34" className="[stop-color:var(--cb-brand)]" />
          <stop offset="1" stopOpacity="0" className="[stop-color:var(--cb-brand)]" />
        </radialGradient>
      </defs>
      <circle className="flywheel__glow" cx={CENTRE} cy={CENTRE} r={CENTRE} fill={`url(#${glow})`} />
      <circle className="flywheel__ring" cx={CENTRE} cy={CENTRE} r={RING} />
      <g className="flywheel__bars">
        {Array.from({ length: BARS }, (_, i) => (
          <g key={i} transform={`rotate(${short((i / BARS) * 360)} ${String(CENTRE)} ${String(CENTRE)})`}>
            <rect x={CENTRE - 1} y={CENTRE - BAR_FROM - BAR_LENGTH} width={2} height={BAR_LENGTH} rx={1} />
          </g>
        ))}
      </g>
      <g className="flywheel__marker">
        {Array.from({ length: TAIL }, (_, s) => (
          <path
            key={s}
            d={`M ${onRing(-(s + 1) * TAIL_STEP)} A ${String(RING)} ${String(RING)} 0 0 1 ${onRing(-s * TAIL_STEP)}`}
            opacity={short(0.9 * (1 - s / TAIL))}
          />
        ))}
        <circle cx={CENTRE + RING} cy={CENTRE} r={2.8} />
      </g>
    </svg>
  )
}
