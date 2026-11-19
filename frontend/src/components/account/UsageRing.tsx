import { useId } from 'react'
import { useT } from '../../i18n/I18n'

const RADIUS = 15.9155 // a circumference of 100, so the dash length is the percentage

/**
 * The month's classes used as a ring in the heat line (ADR 0013 Afterburn A3), the figures in the
 * middle. Presentational; nothing moves, so reduced motion needs no case. It sits on the Plan card
 * (`bg-raised`), so the track is the muted colour mixed 70% into the card colour (3:1 or more in both
 * themes; the line and surface colours are 1.1 to 1.2:1 and left it invisible at 0 of N, issue 1277).
 * 5.5rem on a phone, 8rem from `sm`; `sm:row-span-2` spans the card's two text rows there.
 */
export function UsageRing({ held, limit }: { held: number; limit: number }) {
  const t = useT()
  const id = `ring-${useId().replace(/[^a-zA-Z0-9]/g, '')}`
  const percent = limit > 0 ? Math.min(100, Math.round((held / limit) * 100)) : 0
  return (
    <div
      role="img"
      aria-label={t('plan.usedRing', { percent })}
      data-testid="usage-ring"
      className="relative size-[5.5rem] shrink-0 sm:size-32 sm:row-span-2"
    >
      <svg viewBox="0 0 36 36" className="size-full -rotate-90" aria-hidden="true">
        <defs>
          <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" className="[stop-color:var(--cb-heat-from)]" />
            <stop offset="0.55" className="[stop-color:var(--cb-heat-mid)]" />
            <stop offset="1" className="[stop-color:var(--cb-heat-to)]" />
          </linearGradient>
        </defs>
        <circle
          cx="18"
          cy="18"
          r={RADIUS}
          fill="none"
          strokeWidth="3.5"
          className="[stroke:color-mix(in_srgb,var(--cb-muted)_70%,var(--cb-raised))]"
        />
        {percent > 0 ? (
          <circle
            cx="18"
            cy="18"
            r={RADIUS}
            fill="none"
            stroke={`url(#${id})`}
            strokeWidth="3.5"
            strokeLinecap="round"
            strokeDasharray={`${String(percent)} 100`}
          />
        ) : null}
      </svg>
      <span
        aria-hidden="true"
        className="absolute inset-0 flex flex-col items-center justify-center font-display text-xl font-extrabold sm:text-2xl tabular-nums"
      >
        {held}
        <span className="font-sans text-xs font-medium text-muted">/ {limit}</span>
      </span>
    </div>
  )
}
