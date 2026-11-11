import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useT } from '../../i18n/I18n'
import { createDesignJob, type Brief, type DesignInput, type Playlist } from '../../api/jobs'
import { startError } from '../../api/songMessages'
import { createPickJob, type PickInput } from '../../api/songs'
import type { SwedebeatMix } from '../../api/swedebeatMixes'
import type { Connection } from '../../api/connections'
import { MusicChoice, type Music, type SongsAvailability, type SwedebeatMixAvailability } from '../songs/MusicChoice'
import { MixTiles } from './MixTiles'
import { Choice } from './Choice'
import { createError } from './createError'
import { PlanNudge } from '../plans/PlanNudge'
import { UpgradeLink } from '../plans/UpgradeLink'
import { leftOut, validateForm, whole } from './formModel'
import { TextArea, TextInput } from './inputs'
import { LengthChoice } from './LengthChoice'
import { PlaylistTiles, usePlaylistCovers } from './PlaylistTiles'
import { SideCard } from './SideCard'
import { RideCounts } from './RideCounts'
import { StylePicker } from './StylePicker'
import { summaryRows } from './summary'
import { usePlanAllowance } from './usePlanAllowance'
import { Notice, useAction } from '../forms'
import { Card, CardTitle } from '../ui/card'
import { EFFORTS, sketchProfile } from '../../lib/sketch'
import { newIdempotencyKey, rememberInput, rememberPick } from '../../lib/submittedInput'
import { readPreset } from '../../lib/presets'
import { useQuery, useRouter } from '../../router/Router'

export function AdvancedForm({
  connection,
  playlists,
  songs,
  swedebeat,
  mixes,
  runningEnabled,
  soundlessEnabled,
}: {
  connection: Connection
  playlists: Playlist[]
  songs: SongsAvailability
  swedebeat: SwedebeatMixAvailability
  mixes: SwedebeatMix[]
  /** The effective `running_enabled` flag: without it the form has no Running at all. */
  runningEnabled: boolean
  /** The effective `soundless_enabled` flag: without it the form has no "No music". */
  soundlessEnabled: boolean
}) {
  const t = useT()
  const { navigate } = useRouter()
  const preset = readPreset(useQuery())
  const action = useAction()
  const [title, setTitle] = useState('')
  const [playlist, setPlaylist] = useState('')
  // Playlist covers only for a user with a Spotify connection (#537); initials until they arrive.
  const covers = usePlaylistCovers(playlists.length > 0 && (songs.kind === 'ready' || songs.kind === 'needs_consent'))
  const [mixId, setMixId] = useState('')
  const [music, setMusic] = useState<Music>('playlist')
  const [sport, setSport] = useState<'cycling' | 'running'>('cycling')
  // Running is never on the form while the flag is off, whatever the state says.
  const running = runningEnabled && sport === 'running'
  // "No music" is cycling only and never on the form while the flag is off, whatever the state says.
  const noMusic = soundlessEnabled && !running && music === 'none'
  const [length, setLength] = useState(preset.length)
  const [intensity, setIntensity] = useState<Brief['intensity']>(preset.intensity)
  const [climbs, setClimbs] = useState(preset.climbs)
  const [sprints, setSprints] = useState(preset.sprints)
  const [races, setRaces] = useState(preset.races)
  const [ftp, setFtp] = useState('')
  const [style, setStyle] = useState('')
  const [notes, setNotes] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  // The notices sit at the top of a long form, the button in the bar: bring a new one into view.
  const notices = useRef<HTMLDivElement>(null)
  const { classesLeft, allowed, plan } = usePlanAllowance()
  const shown = problem ?? action.error
  useEffect(() => {
    if (shown !== null) notices.current?.scrollIntoView({ block: 'nearest' })
  }, [shown])
  const racesOn = allowed?.gates.races ?? true
  const ftpOn = allowed?.gates.ftpTests ?? true
  const gamesOn = allowed?.gates.games ?? true
  const leftOutText = leftOut(t, [
    ...(racesOn ? [] : [t('newclass.planLeavesOut.races')]),
    ...(ftpOn ? [] : [t('newclass.planLeavesOut.ftpTests')]),
  ])
  // One key per user action: the same form values keep the key, so a repeated click or a retry
  // after a lost answer returns the same job instead of starting a second one.
  const attempt = useRef<{ fingerprint: string; key: string } | null>(null)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const checked = validateForm(t, {
      title, playlist, mixId, music, running, noMusic, length, intensity, climbs, sprints, races, ftp, style, notes,
      racesOn,
      ftpOn,
      maxMinutes: allowed?.maxMinutes ?? null,
    })
    setProblem('problem' in checked ? checked.problem : null)
    if ('problem' in checked) return
    const fullBrief = checked.brief
    const name = title.trim()
    if (!running && !noMusic && 'climbs' in fullBrief && music === 'pick' && songs.kind === 'ready') {
      const pickInput: PickInput = {
        title: name,
        brief: fullBrief,
        mode: 'pick',
        mowl_connection_id: connection.id,
        spotify_connection_id: songs.spotifyConnectionId,
      }
      const pickFingerprint = JSON.stringify(pickInput)
      if (attempt.current?.fingerprint !== pickFingerprint) {
        attempt.current = { fingerprint: pickFingerprint, key: newIdempotencyKey() }
      }
      const pickKey = attempt.current.key
      void action.run(async () => {
        const job = await createPickJob(pickInput, pickKey)
        rememberPick(job.id, pickInput)
        navigate(`/jobs/${job.id}`)
      }, startError)
      return
    }
    const musicInput: DesignInput['music'] = noMusic
      ? { source: 'none' }
      : !running && music === 'swedebeat_mix'
        ? { source: 'mowl_swedebeat_mix', mix_id: mixId }
        : { source: 'mowl_spotify_playlist', spotify_playlist_id: playlist }
    const input: DesignInput = {
      title: name,
      brief: fullBrief,
      music: musicInput,
      mowl_connection_id: connection.id,
    }
    const fingerprint = JSON.stringify(input)
    if (attempt.current?.fingerprint !== fingerprint) {
      attempt.current = { fingerprint, key: newIdempotencyKey() }
    }
    const key = attempt.current.key
    void action.run(async () => {
      const job = await createDesignJob(input, key)
      rememberInput(job.id, input)
      navigate(`/jobs/${job.id}`)
    }, (error) => createError(t, error, noMusic))
  }

  const summary = summaryRows(t, {
    length,
    intensity,
    climbs,
    sprints,
    races,
    racesOn,
    running,
    noMusic,
    picking: !running && !noMusic && music === 'pick' && songs.kind === 'ready',
    onMix: !running && !noMusic && music === 'swedebeat_mix' && swedebeat.kind === 'ready',
    chosenMix: mixes.find((m) => m.mixId === mixId),
    chosenPlaylist: playlists.find((p) => p.spotifyPlaylistId === playlist),
  })
  const onSwedebeatMix = !running && !noMusic && music === 'swedebeat_mix' && swedebeat.kind === 'ready'
  const profile = sketchProfile({
    length: whole(length),
    effort: intensity,
    climbs: running ? 0 : whole(climbs),
    sprints: running ? 0 : whole(sprints),
    races: running || !racesOn ? 0 : whole(races),
  })

  if (
    playlists.length === 0 &&
    (running ||
      (!soundlessEnabled && songs.kind !== 'ready' && swedebeat.kind !== 'ready' && swedebeat.kind !== 'failed'))
  ) {
    return (
      <Card>
        <CardTitle>{t('newclass.noPlaylists.title')}</CardTitle>
        <p className="m-0 text-muted">{t('newclass.noPlaylists.body')}</p>
      </Card>
    )
  }
  const section = 'flex flex-col gap-4'
  return (
    <form
      className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]"
      onSubmit={submit}
      noValidate
      aria-label={t('newclass.formLabel')}
    >
      <div className="flex min-w-0 flex-col gap-5">
        <div ref={notices} className="flex flex-col gap-5 empty:hidden">
          {problem ? <Notice kind="error">{problem}</Notice> : null}
          {action.error ? <Notice kind="error">{action.error}</Notice> : null}
          {action.upgrade ? <UpgradeLink /> : null}
          <PlanNudge trigger="low" plan={plan} />
        </div>
        <Card className={section}>
          <CardTitle className="mb-0">{t('newclass.basics')}</CardTitle>
          {runningEnabled ? (
            <Choice
              label={t('newclass.sport')}
              options={[
                { value: 'cycling', label: t('newclass.sport.cycling') },
                { value: 'running', label: t('newclass.sport.running') },
              ]}
              value={running ? 'running' : 'cycling'}
              onValueChange={(next) => {
                setSport(next)
                if (next === 'running') setMusic('playlist')
              }}
            >
              {running ? <p className="m-0 text-[0.9375rem] text-muted">{t('newclass.sport.runningHint')}</p> : null}
            </Choice>
          ) : null}
          <TextInput
            label={t('newclass.className')}
            value={title}
            maxLength={100}
            onChange={(e) => {
              setTitle(e.target.value)
            }}
            required
          />
          <LengthChoice length={length} onChange={setLength} maxMinutes={allowed?.maxMinutes ?? null} />
        </Card>
        <Card className={section}>
          <CardTitle className="mb-0">{t('newclass.music')}</CardTitle>
          {running ? null : (
            <MusicChoice
              songs={songs}
              swedebeat={swedebeat}
              soundless={soundlessEnabled}
              value={music}
              onChange={setMusic}
            />
          )}
          {!noMusic && (running || music === 'playlist') ? (
            <PlaylistTiles playlists={playlists} value={playlist} onChange={setPlaylist} covers={covers} />
          ) : null}
          {onSwedebeatMix ? <MixTiles mixes={mixes} value={mixId} onChange={setMixId} /> : null}
          <StylePicker value={style} onChange={setStyle} />
        </Card>
        <Card className={section}>
          <CardTitle className="mb-0">{running ? t('newclass.theRun') : t('newclass.theRide')}</CardTitle>
          <Choice
            label={t('newclass.effort')}
            options={EFFORTS.map((e) => ({ value: e.value, label: t(e.labelKey) }))}
            value={intensity}
            onValueChange={setIntensity}
          />
          {running ? null : (
            <RideCounts
              climbs={climbs}
              sprints={sprints}
              races={races}
              ftp={ftp}
              racesOn={racesOn}
              ftpOn={ftpOn}
              leftOutText={leftOutText}
              onClimbs={setClimbs}
              onSprints={setSprints}
              onRaces={setRaces}
              onFtp={setFtp}
            />
          )}
        </Card>
        <Card className={section}>
          <TextArea
            label={t('newclass.notes')}
            value={notes}
            maxLength={2000}
            hint={gamesOn ? t('newclass.notes.hint') : t('newclass.notes.hintNoGames')}
            onChange={(e) => {
              setNotes(e.target.value)
            }}
          />
        </Card>
      </div>
      <div className="min-w-0 lg:self-stretch">
        <SideCard profile={profile} summary={summary} busy={action.busy} classesLeft={classesLeft} />
      </div>
    </form>
  )
}
