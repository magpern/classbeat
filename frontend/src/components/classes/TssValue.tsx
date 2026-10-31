import { TSS_APPROXIMATE_NOTE } from '../../lib/classCards'

/**
 * A TSS; with any zone 7 interval it reads "≈ N" and carries ADR 0012 section 7's note. The note
 * is visually hidden text, so screen readers read the number with it (an aria-label on a plain
 * span is not reliably read).
 */
export function TssValue({ value, approximate }: { value: number; approximate: boolean }) {
  if (!approximate) return <span>{String(value)}</span>
  return (
    <span title={TSS_APPROXIMATE_NOTE}>
      <span aria-hidden="true">≈ {String(value)}</span>
      <span className="sr-only">{`${String(value)}, ${TSS_APPROXIMATE_NOTE}`}</span>
    </span>
  )
}
