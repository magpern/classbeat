import type { ReactNode } from 'react'

/**
 * The brief box: a 2 px heat frame round the words that glows while the box has focus (ADR 0013 Afterburn A3). The
 * glow is a shadow that only transitions when motion is welcome; the frame also gets a focus outline.
 */
export function BriefFrame({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-[1.75rem] bg-heat p-0.5 focus-within:shadow-glow focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-brand motion-safe:transition-shadow motion-safe:duration-200">
      <div className="flex flex-col gap-3 rounded-[1.625rem] bg-surface p-5 pb-4">{children}</div>
    </div>
  )
}

/** The classes of the brief's text area inside the frame: no box of its own, a larger size. */
export const BRIEF_AREA = 'border-0 bg-transparent px-0 text-xl leading-snug focus-visible:outline-none'
