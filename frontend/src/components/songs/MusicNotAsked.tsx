import type { InterpretationItem } from '../../api/interpretationItems'
import { useT } from '../../i18n/I18n'
import { interpretationNote } from '../../lib/interpretationNote'

/**
 * What the chosen songs could not do as asked (issue #1133): too few songs from an era the
 * instructor named, songs from other years, a list shorter than the class. Shown above the songs
 * before Approve, worded from the catalogue by path and reason code; nothing the server or a
 * model wrote is shown.
 */
export function MusicNotAsked({ items }: { items: readonly InterpretationItem[] }) {
  const t = useT()
  const lines = interpretationNote(items).notAsked
  if (lines.length === 0) return null
  return (
    <div className="notice" data-kind="info" role="status" data-testid="music-not-asked">
      <p className="m-0">{t('songs.approval.notAsked')}</p>
      <ul className="m-0 pl-5">
        {lines.map((line) => (
          <li key={line.id}>{line.text}</li>
        ))}
      </ul>
    </div>
  )
}
