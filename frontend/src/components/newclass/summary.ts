import type { TFunction } from '../../i18n/t'
import type { SwedebeatMix } from '../../api/swedebeatMixes'
import type { Playlist } from '../../api/jobs'
import { EFFORTS, type Effort } from '../../lib/sketch'
import { whole } from './formModel'

export interface SummaryInput {
  length: string
  intensity: Effort
  climbs: string
  sprints: string
  races: string
  racesOn: boolean
  running: boolean
  noMusic: boolean
  /** "Pick for me" is chosen and available. */
  picking: boolean
  /** A Swedebeat mix is the music source and is available. */
  onMix: boolean
  chosenMix: SwedebeatMix | undefined
  chosenPlaylist: Playlist | undefined
}

/** The rows of "Your class so far": what the form says now, in the words the form uses. */
export function summaryRows(t: TFunction, s: SummaryInput): { label: string; value: string }[] {
  const length = whole(s.length)
  const effort = EFFORTS.find((e) => e.value === s.intensity)
  let music: string
  if (s.noMusic) music = t('newclass.summary.noMusic')
  else if (s.picking) music = t('newclass.summary.songsPicked')
  else if (s.onMix) music = s.chosenMix ? s.chosenMix.name || t('newclass.untitledMix') : t('newclass.summary.noMix')
  else music = s.chosenPlaylist ? s.chosenPlaylist.name || t('newclass.untitledPlaylist') : t('newclass.summary.noPlaylist')
  return [
    {
      label: t('newclass.summary.length'),
      value: Number.isInteger(length) ? t('newclass.minutes', { count: length }) : t('newclass.notSet'),
    },
    { label: t('newclass.effort'), value: effort === undefined ? '' : t(effort.labelKey) },
    ...(s.running
      ? [{ label: t('newclass.sport'), value: t('newclass.sport.running') }]
      : [
          { label: t('newclass.climbs'), value: s.climbs },
          { label: t('newclass.sprints'), value: s.sprints },
          { label: t('newclass.races'), value: s.racesOn ? s.races : '0' },
        ]),
    { label: t('newclass.summary.music'), value: music },
  ]
}
