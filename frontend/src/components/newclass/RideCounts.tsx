import { useT } from '../../i18n/I18n'
import { SelectInput } from './inputs'
import { Stepper } from './Stepper'

/** The cycling-only part of the ride card: climbs, sprints, races, the FTP test and what the plan leaves out. */
export function RideCounts({
  climbs, sprints, races, ftp, racesOn, ftpOn, leftOutText, onClimbs, onSprints, onRaces, onFtp,
}: {
  climbs: string
  sprints: string
  races: string
  ftp: string
  racesOn: boolean
  ftpOn: boolean
  leftOutText: string | null
  onClimbs: (value: string) => void
  onSprints: (value: string) => void
  onRaces: (value: string) => void
  onFtp: (value: string) => void
}) {
  const t = useT()
  return (
    <>
      <div className="grid gap-4 sm:grid-cols-3">
        <Stepper
          label={t('newclass.climbs')}
          fewer={t('newclass.climbs.fewer')}
          more={t('newclass.climbs.more')}
          value={climbs}
          min={0}
          max={10}
          onChange={onClimbs}
        />
        <Stepper
          label={t('newclass.sprints')}
          fewer={t('newclass.sprints.fewer')}
          more={t('newclass.sprints.more')}
          value={sprints}
          min={0}
          max={20}
          onChange={onSprints}
        />
        {racesOn ? (
          <Stepper
            label={t('newclass.races')}
            fewer={t('newclass.races.fewer')}
            more={t('newclass.races.more')}
            value={races}
            min={0}
            max={5}
            onChange={onRaces}
          />
        ) : null}
      </div>
      {ftpOn ? (
        <SelectInput
          label={t('newclass.ftp')}
          value={ftp}
          onChange={(e) => {
            onFtp(e.target.value)
          }}
        >
          <option value="">{t('newclass.ftp.none')}</option>
          <option value="3">{t('newclass.ftp.minutes', { count: 3 })}</option>
          <option value="5">{t('newclass.ftp.minutes', { count: 5 })}</option>
          <option value="20">{t('newclass.ftp.minutes', { count: 20 })}</option>
        </SelectInput>
      ) : null}
      {leftOutText === null ? null : <p className="m-0 text-[0.875rem] text-muted">{leftOutText}</p>}
    </>
  )
}
