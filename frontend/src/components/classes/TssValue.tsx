import { TSS_APPROXIMATE_NOTE } from '../../lib/classCards'

/**
 * A TSS. With any zone 7 interval it reads "≈ N" and says why: the visible mark is hidden from
 * screen readers, which get the number and ADR 0012 section 7's note as text instead.
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
