import type { InterpretationItem } from '../../api/interpretationItems'
import { useT } from '../../i18n/I18n'
import { interpretationNote } from '../../lib/interpretationNote'

/**
 * What the version in front of the instructor does not do as asked, shown before Approve and before
 * Save (ADR 0029 amendment of 2026-11-24, issue #1494), so a difference is seen before it is
 * accepted. Worded from the catalogue by path and reason code, like the reading.
 */
export function NotAskedBefore({ items, before }: { items: readonly InterpretationItem[]; before: 'approve' | 'save' }) {
  const t = useT()
  const lines = interpretationNote(items).notAsked
  if (lines.length === 0) return null
  return (
    <div className="notice" data-kind="info" role="status" data-testid="not-asked-before">
      <p className="m-0">{before === 'approve' ? t('cw.approve.notAsked') : t('cw.save.notAsked')}</p>
      <ul className="m-0 pl-5">
        {lines.map((line) => (
          <li key={line.id}>{line.text}</li>
        ))}
      </ul>
    </div>
  )
}
