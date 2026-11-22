import { decimal } from '../../i18n/format'
import { useT } from '../../i18n/I18n'
import { clock } from '../../lib/workout'
import { mapStudio, NO_PROVENANCE, type PieceMapping, type StudioClip } from '../../preview/timeModel'

interface Props {
  clips: StudioClip[]
  piece: number | null
  /** The observed cursor's class time; null: none. */
  cursorMs: number | null
}

function fact(value: string | number | null, unit = ''): string | null {
  return value === null ? null : `${String(value)}${unit}`
}

/**
 * Where a position of a song comes from, shown while a song of the lane is hovered or focused: the
 * analysis, the alignment and the instructor's correction, each its own line and never merged into
 * one number. A fact the API does not give the page says "not recorded"; none is invented.
 */
export function ProvenanceCard({ clips, piece, cursorMs }: Props) {
  const t = useT()
  const clip = piece === null ? undefined : clips.find((c) => c.piece === piece)
  if (clip === undefined) return <p className="m-0 text-muted" data-testid="provenance-hint">{t('editor.prov.hint')}</p>
  const at = cursorMs !== null && cursorMs >= clip.startMs && cursorMs < clip.endMs ? cursorMs : clip.startMs
  const mapped: PieceMapping | undefined = mapStudio(clips, at).find((m) => m.piece === clip.piece)
  const prov = mapped?.provenance ?? clip.provenance ?? NO_PROVENANCE
  const missing = t('editor.prov.missing')
  const rows: [string, string][] = [
    [t('editor.prov.window'), t('editor.prov.windowValue', { from: clock(clip.startMs / 1000), to: clock(clip.endMs / 1000) })],
    [t('editor.prov.transport'), t('editor.prov.transportValue', { sec: decimal((mapped?.transportMs ?? 0) / 1000, 2) })],
    [t('editor.prov.source'), mapped?.sourceMs == null ? missing : t('editor.prov.sourceValue', { sec: decimal(mapped.sourceMs / 1000, 2) })],
    [t('editor.prov.gain'), t('editor.prov.gainValue', { pct: Math.round((mapped?.gain ?? 0) * 100), role: t(`editor.prov.role.${mapped?.role ?? 'solo'}`) })],
    [t('editor.prov.map'), fact(prov.analysis.mapVersion) ?? missing],
    [t('editor.prov.job'), fact(prov.analysis.spotdownJob) ?? missing],
    [t('editor.prov.timing'), fact(prov.analysis.timingClass) ?? missing],
    [t('editor.prov.alignment'), fact(prov.alignment.offsetMs, ' ms') ?? missing],
    [t('editor.prov.correction'), prov.correction === null ? t('editor.prov.noCorrection') : `${String(prov.correction.offsetMs)} ms`],
  ]
  return (
    <div className="rounded-md border border-line bg-surface p-3" data-testid="provenance" aria-live="polite">
      <p className="m-0 font-medium text-text">{t('editor.prov.title', { n: clip.piece + 1, title: clip.label })}</p>
      <dl className="m-0 mt-2 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-[0.9375rem]">
        {rows.map(([name, value]) => (
          <div key={name} className="contents">
            <dt className="text-muted">{name}</dt>
            <dd className="m-0 text-text">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
