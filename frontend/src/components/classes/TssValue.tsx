import { TSS_APPROXIMATE_NOTE } from '../../lib/classCards'

/** A TSS; with any zone 7 interval it reads "≈ N" and carries ADR 0012 section 7's note. */
export function TssValue({ value, approximate }: { value: number; approximate: boolean }) {
  if (!approximate) return <span>{String(value)}</span>
  return (
    <span aria-label={`${String(value)}, ${TSS_APPROXIMATE_NOTE}`} title={TSS_APPROXIMATE_NOTE}>
      <span aria-hidden="true">≈ {String(value)}</span>
    </span>
  )
}
