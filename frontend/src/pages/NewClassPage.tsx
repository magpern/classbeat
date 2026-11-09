import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Trans, useT } from '../i18n/I18n'
import {
  createDesignJob,
  type Brief,
  type DesignInput,
  type Playlist,
} from '../api/jobs'
import { startError } from '../api/songMessages'
import { createPickJob, type PickInput } from '../api/songs'
import { getPlan, type Gates } from '../api/plan'
import type { SwedebeatMix } from '../api/swedebeatMixes'
import type { Connection } from '../api/connections'
import { MusicChoice, type Music, type SongsAvailability, type SwedebeatMixAvailability } from '../components/songs/MusicChoice'
import { MixTiles } from '../components/newclass/MixTiles'
import { Choice } from '../components/newclass/Choice'
import { createError } from '../components/newclass/createError'
import { leftOut, validateForm, whole } from '../components/newclass/formModel'
import { SelectInput, TextArea, TextInput } from '../components/newclass/inputs'
import { LengthChoice } from '../components/newclass/LengthChoice'
import { PlaylistTiles, usePlaylistCovers } from '../components/newclass/PlaylistTiles'
import { SideCard } from '../components/newclass/SideCard'
import { loadSource, type Source } from '../components/newclass/source'
import { Stepper } from '../components/newclass/Stepper'
import { StylePicker } from '../components/newclass/StylePicker'
import { summaryRows } from '../components/newclass/summary'
import { Notice, useAction } from '../components/forms'
import { Button } from '../components/ui/button'
import { Card, CardTitle } from '../components/ui/card'
import { EFFORTS, sketchProfile } from '../lib/sketch'
import { RequireAuth } from '../components/RequireAuth'
import { newIdempotencyKey, rememberInput, rememberPick } from '../lib/submittedInput'
import { readPreset } from '../lib/presets'
import { Link, useQuery, useRouter } from '../router/Router'

export function NewClassPage() {
  return (
    <RequireAuth>
      <NewClass />
    </RequireAuth>
  )
}

function NewClass() {
  const t = useT()
  const [source, setSource] = useState<Source>({ status: 'loading' })
  const [reload, setReload] = useState(0)

  useEffect(() => {
    const abort = new AbortController()
    void loadSource(abort.signal).then((loaded) => {
      if (loaded !== null && !abort.signal.aborted) setSource(loaded)
    })
    return () => {
      abort.abort()
    }
  }, [reload])

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-5">
      <h1 className="m-0 font-display text-3xl font-extrabold">{t('newclass.title')}</h1>
      {source.status === 'loading' ? <p className="m-0 text-muted">{t('newclass.loading')}</p> : null}
      {source.status === 'no_mowl' ? (
        <Notice>
          <Trans
            id={source.reconnect ? 'newclass.noMowl.reconnect' : 'newclass.noMowl.connect'}
            slots={{
              link: (
                <Link to="/account">
                  {t(source.reconnect ? 'newclass.noMowl.reconnectLink' : 'newclass.noMowl.connectLink')}
                </Link>
              ),
            }}
          />
        </Notice>
      ) : null}
      {source.status === 'playlists_failed' ? (
        <>
          <Notice kind="error">{t(source.message)}</Notice>
          <p className="m-0">
            <Button
              variant="secondary"
              onClick={() => {
                setSource({ status: 'loading' })
                setReload((n) => n + 1)
              }}
            >
              {t('common.tryAgain')}
            </Button>
          </p>
        </>
      ) : null}
      {source.status === 'ready' ? (
        <ClassForm
          connection={source.connection}
          playlists={source.playlists}
          songs={source.songs}
          swedebeat={source.swedebeat}
          mixes={source.mixes}
          runningEnabled={source.runningEnabled}
          soundlessEnabled={source.soundlessEnabled}
        />
      ) : null}
    </div>
  )
}


function ClassForm({
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
  // Classes left this month, from the plan API; hidden while unknown or when the call fails.
  const [classesLeft, setClassesLeft] = useState<number | null>(null)
  // What the plan allows (#496), shown before the wall; unknown means no cap is shown here and the
  // server still enforces it.
  const [allowed, setAllowed] = useState<{ maxMinutes: number; gates: Gates } | null>(null)
  useEffect(() => {
    const abort = new AbortController()
    getPlan(abort.signal).then(
      (plan) => {
        if (!abort.signal.aborted) {
          setClassesLeft(Math.max(0, plan.limits.classesPerMonth - plan.classesHeld))
          setAllowed({ maxMinutes: plan.limits.maxClassMinutes, gates: plan.gates })
        }
      },
      () => undefined,
    )
    return () => {
      abort.abort()
    }
  }, [])
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
            <>
              <div className="grid gap-4 sm:grid-cols-3">
                <Stepper
                  label={t('newclass.climbs')}
                  fewer={t('newclass.climbs.fewer')}
                  more={t('newclass.climbs.more')}
                  value={climbs}
                  min={0}
                  max={10}
                  onChange={setClimbs}
                />
                <Stepper
                  label={t('newclass.sprints')}
                  fewer={t('newclass.sprints.fewer')}
                  more={t('newclass.sprints.more')}
                  value={sprints}
                  min={0}
                  max={20}
                  onChange={setSprints}
                />
                {racesOn ? (
                  <Stepper
                    label={t('newclass.races')}
                    fewer={t('newclass.races.fewer')}
                    more={t('newclass.races.more')}
                    value={races}
                    min={0}
                    max={5}
                    onChange={setRaces}
                  />
                ) : null}
              </div>
              {ftpOn ? (
                <SelectInput
                  label={t('newclass.ftp')}
                  value={ftp}
                  onChange={(e) => {
                    setFtp(e.target.value)
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
