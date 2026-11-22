import { useState } from 'react'
import type { Alignment, AlignmentSong, PreviewFailure } from '../../../api/spotifyPreview'
import { decimal, plain } from '../../../i18n/format'
import { useT } from '../../../i18n/I18n'
import { NUDGE_STEPS_MS, nudged, provenanceOf } from '../../../preview/spotifyAlignmentModel'
import { describeFailure } from '../../../preview/spotifyErrorModel'
import { Notice } from '../../forms'
import { Button } from '../../ui/button'

interface Props {
  alignment: Alignment
  /** The song shown first: the one playing, when one is. */
  initialPiece: number | null
  saving: boolean
  failure: PreviewFailure | null
  onCorrect: (song: number, offsetMs: number | null) => void
}

const seconds = (ms: number): string => decimal(ms / 1000, 1)

/** The facts of a song's timing, each on its own line: spotdown's analysis, its alignment and the instructor's correction, never added into one. */
function Provenance({ song, mapVersion }: { song: AlignmentSong; mapVersion: number | null }) {
  const t = useT()
  const p = provenanceOf(song, mapVersion)
  const missing = t('editor.prov.missing')
  const rows: [string, string][] = [
    [t('editor.prov.map'), p.analysis.mapVersion ?? missing],
    [t('editor.prov.job'), p.analysis.spotdownJob ?? missing],
    [t('editor.prov.timing'), p.analysis.timingClass ?? missing],
    [t('editor.prov.alignment'), p.alignment.offsetMs === null ? missing : `${String(p.alignment.offsetMs)} ms`],
    [
      t('editor.prov.correction'),
      p.correction === null ? t('editor.prov.noCorrection') : `${String(p.correction.offsetMs)} ms${p.correction.setAgainstAnalysis === null ? '' : ` (${p.correction.setAgainstAnalysis})`}`,
    ],
  ]
  return (
    <div data-testid="spotify-provenance">
      <p className="m-0 font-medium text-text">{t('preview.prov.title', { n: song.song })}</p>
      <dl className="m-0 mt-2 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-[0.9375rem]">
        {rows.map(([name, value]) => (
          <div key={name} className="contents">
            <dt className="text-muted">{name}</dt>
            <dd className="m-0 text-text">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="m-0 mt-2 text-muted">{t('preview.prov.note')}</p>
    </div>
  )
}

/**
 * Fine-tuning of a song's timing by listening: nudge buttons, a confirm when the analysis changed
 * since the correction was set ("listen again": shown, not used until confirmed), and a clear. It
 * only moves the cursor here; the class and its saved programs do not change.
 */
export function CorrectionPanel({ alignment, initialPiece, saving, failure, onCorrect }: Props) {
  const t = useT()
  const [piece, setPiece] = useState(initialPiece ?? alignment.songs[0]?.piece ?? 0)
  const song = alignment.songs.find((s) => s.piece === piece) ?? alignment.songs[0]
  if (song === undefined) return null
  const stored = song.correction?.offsetMs ?? 0
  const again = song.correction?.state === 'listen_again'
  return (
    <section className="flex flex-col gap-3 rounded-md border border-line bg-surface p-3" aria-label={t('preview.corr.title', { n: song.song })} data-testid="correction-panel">
      <h3 className="m-0 font-medium text-text">{t('preview.corr.title', { n: song.song })}</h3>
      <p className="m-0 text-muted">{t('preview.corr.intro')}</p>
      <label className="flex flex-wrap items-center gap-2">
        <span>{t('preview.corr.pick')}</span>
        <select className="min-h-11 rounded-md border border-line bg-surface px-2" value={song.piece} onChange={(e) => setPiece(Number(e.target.value))} data-testid="correction-song">
          {alignment.songs.map((s) => (
            <option key={s.piece} value={s.piece}>
              {s.song}. {s.title}
            </option>
          ))}
        </select>
      </label>
      <p className="m-0" data-testid="correction-value">
        {song.correction === null ? t('preview.corr.none') : t('preview.corr.value', { value: seconds(song.correction.offsetMs) })}
      </p>
      {again ? <Notice>{t('preview.corr.listenAgain')}</Notice> : null}
      <div className="flex flex-wrap gap-2" role="group" aria-label={t('preview.corr.title', { n: song.song })}>
        {[-1, 1].flatMap((sign) =>
          NUDGE_STEPS_MS.map((step) => {
            const delta = `${sign < 0 ? '−' : '+'}${plain(step / 1000)}`
            return (
              <Button key={`${String(sign)}${String(step)}`} variant="secondary" disabled={saving} aria-label={t('preview.corr.step', { delta })} data-testid={`nudge-${sign < 0 ? 'minus' : 'plus'}-${String(step)}`} onClick={() => onCorrect(song.song, nudged(stored, sign * step))}>
                {delta}
              </Button>
            )
          }),
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        {again ? (
          <Button disabled={saving} onClick={() => onCorrect(song.song, stored)} data-testid="correction-confirm">
            {t('preview.corr.confirm')}
          </Button>
        ) : null}
        {song.correction === null ? null : (
          <Button variant="ghost" disabled={saving} onClick={() => onCorrect(song.song, null)} data-testid="correction-clear">
            {t('preview.corr.clear')}
          </Button>
        )}
      </div>
      {saving ? <p className="m-0 text-muted" role="status">{t('preview.corr.saving')}</p> : null}
      {failure === null ? null : <Notice kind="error">{t(describeFailure(failure).key)}</Notice>}
      <Provenance song={song} mapVersion={alignment.mapVersion} />
    </section>
  )
}
