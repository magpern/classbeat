import type { ClassSummary } from '../../api/classes'
import { useT } from '../../i18n/I18n'
import { lengthLabel, profileSeconds, profileTss } from '../../lib/classCards'
import { TssValue } from './TssValue'

/**
 * Time and TSS of a class with a version, as eyebrow labels over numbers; with `showSongs` the song
 * count too, when the list knows it (#1234).
 */
export function ClassFacts({ cls, showSongs = false }: { cls: ClassSummary; showSongs?: boolean }) {
  const t = useT()
  const seconds = profileSeconds(cls.profile)
  if (seconds <= 0) return null
  const songs = showSongs ? cls.songCount : null
  return (
    <dl className={`m-0 grid gap-3 tabular-nums ${songs === null ? 'grid-cols-2' : 'grid-cols-3'}`}>
      <div className="flex flex-col gap-0.5">
        <dt className="text-xs font-semibold tracking-[0.14em] text-muted uppercase">{t('class.list.time')}</dt>
        <dd className="m-0 text-xl font-bold">{lengthLabel(seconds)}</dd>
      </div>
      <div className="flex flex-col gap-0.5">
        <dt className="text-xs font-semibold tracking-[0.14em] text-muted uppercase">{t('class.list.tss')}</dt>
        <dd className="m-0 text-xl font-bold">
          <TssValue value={profileTss(cls.profile)} approximate={cls.tssApproximate} />
        </dd>
      </div>
      {songs !== null && (
        <div className="flex flex-col gap-0.5">
          <dt className="text-xs font-semibold tracking-[0.14em] text-muted uppercase">{t('class.list.songs')}</dt>
          <dd className="m-0 text-xl font-bold">{songs}</dd>
        </div>
      )}
    </dl>
  )
}
