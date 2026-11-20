const RADIUS = 15

/**
 * The code's remaining time as a ring that empties (ADR 0013 Afterburn A3). It is redrawn once a
 * second with the countdown text, never animated, so reduced motion needs no case. Decorative: the
 * text beside it carries the time.
 */
export function CountdownRing({ remaining, span }: { remaining: number; span: number }) {
  const share = Math.min(1, Math.max(0, remaining / span))
  const length = 2 * Math.PI * RADIUS
  return (
    <svg viewBox="0 0 36 36" className="size-9 shrink-0 -rotate-90" aria-hidden="true">
      <circle cx="18" cy="18" r={RADIUS} fill="none" strokeWidth="4" className="stroke-stage-grid" />
      <circle
        cx="18"
        cy="18"
        r={RADIUS}
        fill="none"
        strokeWidth="4"
        strokeLinecap="round"
        strokeDasharray={`${String(share * length)} ${String(length)}`}
        className="stroke-heat-to"
      />
    </svg>
  )
}
