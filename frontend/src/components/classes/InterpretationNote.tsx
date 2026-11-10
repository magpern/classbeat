import type { InterpretationItem } from '../../api/interpretationItems'
import { useT } from '../../i18n/I18n'
import { interpretationNote, type NoteLine } from '../../lib/interpretationNote'

function Part({ title, lines }: { title: string; lines: NoteLine[] }) {
  const t = useT()
  if (lines.length === 0) return null
  return (
    <section aria-label={title} className="flex flex-col gap-1">
      <h4 className="m-0 text-sm font-semibold uppercase tracking-wide text-muted">{title}</h4>
      <ul className="m-0 flex list-none flex-col gap-1 p-0">
        {lines.map((line) => (
          <li key={line.id} className="m-0">
            {line.text}
            {line.quote !== null ? (
              <span className="block text-sm text-muted">{t('interp.quote', { quote: line.quote })}</span>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  )
}

/**
 * How the request was read (ADR 0029 section 4), in three parts: what was done as asked, what
 * could not be, and what was assumed. Every sentence comes from the catalogue; the instructor's
 * own words appear only as a plain-text quote.
 */
export function InterpretationNote({ items }: { items: readonly InterpretationItem[] }) {
  const t = useT()
  const note = interpretationNote(items)
  if (items.length === 0) return null
  return (
    <section aria-labelledby="interp-title" className="flex flex-col gap-3" data-testid="interpretation">
      <h3 id="interp-title" className="m-0 text-lg font-semibold">
        {t('interp.title')}
      </h3>
      <Part title={t('interp.asked')} lines={note.asked} />
      <Part title={t('interp.notAsked')} lines={note.notAsked} />
      <Part title={t('interp.assumed')} lines={note.assumed} />
    </section>
  )
}
