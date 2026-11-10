import type { DraftSegment } from '../../api/jobs'
import type { Song } from '../../api/songs'
import { kindLabel } from '../../api/classMessages'
import { useT } from '../../i18n/I18n'
import { planSections } from '../../lib/musicPlan'
import { clock } from '../../lib/workout'
import { Card } from '../ui/card'
import { SongStrip } from './SongStrip'

/**
 * The planned songs of each section, one `SongStrip` per section (MA4). Only where the class has an
 * approved song list: a class on one of the user's own playlists or a mix has no per-song timing
 * here, so nothing is drawn for it. The text says "planned": it is where the lengths put the songs,
 * not a measurement of the music.
 */
export function PlannedSongs({ segments, songs }: { segments: DraftSegment[]; songs: Song[] | null }) {
  const t = useT()
  if (songs === null || songs.length === 0) return null
  const sections = planSections(segments, songs).filter((section) => section.songs.length > 0)
  if (sections.length === 0) return null
  return (
    <Card role="region" aria-labelledby="planned-songs-title" className="flex flex-col gap-3 p-4" data-testid="planned-songs">
      <h3 id="planned-songs-title" className="m-0 font-display text-lg font-bold">
        {t('planned.title')}
      </h3>
      <p className="m-0 text-[0.9375rem] text-muted">{t('planned.hint')}</p>
      <ol className="m-0 flex list-none flex-col gap-4 p-0">
        {sections.map((section) => (
          <li key={section.index} data-testid="planned-section" className="flex flex-col gap-1">
            <h4 className="m-0 font-display text-base font-bold">
              {section.name}{' '}
              <span className="text-[0.9375rem] font-normal text-muted">
                {t('planned.range', {
                  kind: kindLabel(section.kind),
                  from: clock(section.startSec),
                  to: clock(section.endSec),
                })}
              </span>
            </h4>
            <SongStrip songs={section.songs} total={section.endSec - section.startSec} left={0} />
            <ul className="m-0 list-none p-0 text-[0.9375rem]">
              {section.songs.map((song) => (
                <li key={song.position} className="break-words">
                  {song.title} <span className="text-muted">{song.artist}</span>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
    </Card>
  )
}
