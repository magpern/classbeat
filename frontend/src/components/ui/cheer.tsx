import type { ReactNode } from 'react'

const BITS = 10

/**
 * A small burst of zone-coloured bits around good news, played once when it is first shown
 * (motion-app.css). Decorative; under reduced motion nothing is drawn. The news itself is `children`.
 */
export function Cheer({ children }: { children: ReactNode }) {
  return (
    <div className="cheer">
      <span className="cheer__bits" aria-hidden="true">
        {Array.from({ length: BITS }, (_, i) => (
          <span key={i} />
        ))}
      </span>
      {children}
    </div>
  )
}
