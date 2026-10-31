// A placeholder class profile on the stage while the class is being made (ADR 0013 section 5).
// The bars are a fixed, invented silhouette in the stage's grid colour: it says nothing about the
// class. It pulses, and stands still under reduced motion.

const HEIGHTS = [22, 30, 38, 30, 46, 54, 38, 62, 46, 70, 54, 38, 30, 22]

export function GhostProfile() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 200 80"
      preserveAspectRatio="none"
      className="block h-28 w-full rounded-md bg-stage motion-safe:animate-pulse"
    >
      {HEIGHTS.map((h, i) => (
        <rect
          key={i}
          x={i * (200 / HEIGHTS.length)}
          y={80 - h}
          width={200 / HEIGHTS.length - 1}
          height={h}
          style={{ fill: 'var(--cb-stage-grid)' }}
        />
      ))}
    </svg>
  )
}
