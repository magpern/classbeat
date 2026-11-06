import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ApiError } from '../api/client'
import { Trans, useT } from '../i18n/I18n'
import type { CatalogueKey } from '../i18n/en'
import type { TFunction } from '../i18n/t'
import { getConnectionsView, type Connection } from '../api/connections'
import { getSwedebeatMixes, swedebeatMixListError, type SwedebeatMix } from '../api/swedebeatMixes'
import {
  createDesignJob,
  getMowlPlaylists,
  type Brief,
  type DesignInput,
  type RunningBrief,
  type Playlist,
} from '../api/jobs'
import { errorMessage } from '../api/messages'
import { startError } from '../api/songMessages'
import { createPickJob, type PickInput } from '../api/songs'
import { getPlan, type Gates } from '../api/plan'
import { getSpotifyStatus } from '../api/spotify'
import {
  MusicChoice,
  type Music,
  type SongsAvailability,
  type SwedebeatMixAvailability,
} from '../components/songs/MusicChoice'
import { MixTiles } from '../components/newclass/MixTiles'
import { EffortCards } from '../components/newclass/EffortCards'
import { SelectInput, TextArea, TextInput } from '../components/newclass/inputs'
import { PlaylistTiles, usePlaylistCovers } from '../components/newclass/PlaylistTiles'
import { SideCard } from '../components/newclass/SideCard'
import { Stepper } from '../components/newclass/Stepper'
import { StylePicker } from '../components/newclass/StylePicker'
import { Notice, useAction } from '../components/forms'
import { Button } from '../components/ui/button'
import { Card, CardTitle } from '../components/ui/card'
import { ChipButton } from '../components/ui/chip'
import { EFFORTS, sketchProfile } from '../lib/sketch'
import { RequireAuth } from '../components/RequireAuth'
import { planRefusalText } from '../api/planMessages'
import { briefBytes, MAX_BRIEF_BYTES } from '../lib/brief'
import { newIdempotencyKey, rememberInput, rememberPick } from '../lib/submittedInput'
import { readPreset } from '../lib/presets'
import { Link, useQuery, useRouter } from '../router/Router'

type Source =
  | { status: 'loading' }
  | { status: 'no_mowl'; reconnect: boolean }
  | { status: 'playlists_failed'; message: CatalogueKey }
  | {
      status: 'ready'
      connection: Connection
      playlists: Playlist[]
      songs: SongsAvailability
      swedebeat: SwedebeatMixAvailability
      mixes: SwedebeatMix[]
      runningEnabled: boolean
      soundlessEnabled: boolean
    }

const PLAYLIST_ERRORS: Record<string, CatalogueKey> = {
  upstream_unavailable: 'newclass.playlistError.unavailable',
  key_unavailable: 'newclass.playlistError.unavailable',
  rate_limited: 'newclass.playlistError.rateLimited',
}

function playlistError(error: unknown): CatalogueKey {
  if (error instanceof ApiError && error.reason !== null && error.status !== 409) {
    const known = Object.hasOwn(PLAYLIST_ERRORS, error.reason) ? PLAYLIST_ERRORS[error.reason] : undefined
    if (known !== undefined) return known
  }
  return 'newclass.playlistError.default'
}

const CREATE_ERRORS: Record<string, CatalogueKey> = {
  not_connected: 'newclass.createError.not_connected',
  class_busy: 'newclass.createError.class_busy',
  idempotency_mismatch: 'newclass.createError.idempotency_mismatch',
  feature_off: 'newclass.createError.feature_off',
  soundless_not_evaluated: 'newclass.createError.soundless_not_evaluated',
  // #509: the designer for the user's plan has not been checked for running classes yet.
  running_not_evaluated: 'newclass.createError.running_not_evaluated',
  invalid_input: 'newclass.createError.invalid_input',
}

/** Whether "Pick songs for me" is offered, and what it still needs (ADR 0008 section 9). */
async function songsAvailability(
  connections: Connection[],
  signal: AbortSignal,
): Promise<SongsAvailability> {
  let status
  try {
    status = await getSpotifyStatus(signal)
  } catch {
    return { kind: 'off' }
  }
  if (status === null) return { kind: 'off' }
  const spotify = connections.find((c) => c.provider === 'spotify')
  if (spotify === undefined || spotify.status !== 'connected' || status.status !== 'connected') {
    return { kind: 'needs_spotify' }
  }
  if (!status.poolConsent) return { kind: 'needs_consent' }
  return { kind: 'ready', spotifyConnectionId: spotify.id }
}

/** `noMusic`: the form asked for "No music", so a `feature_off` is the soundless switch, not running's. */
function createError(t: TFunction, error: unknown, noMusic = false): string {
  const refusal = planRefusalText(error)
  if (refusal !== null) return refusal
  if (noMusic && error instanceof ApiError && error.reason === 'feature_off') return t('newclass.createError.soundlessOff')
  if (error instanceof ApiError && error.reason !== null) {
    const known = Object.hasOwn(CREATE_ERRORS, error.reason) ? CREATE_ERRORS[error.reason] : undefined
    if (known !== undefined) return t(known, { bytes: MAX_BRIEF_BYTES })
  }
  return errorMessage(error)
}

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
    void (async () => {
      try {
        const { connections, swedebeat: swedebeatAccess, runningEnabled, soundlessEnabled } = await getConnectionsView()
        const mowl = connections.find((c) => c.provider === 'mowl')
        if (mowl === undefined || mowl.status !== 'connected') {
          setSource({ status: 'no_mowl', reconnect: mowl !== undefined })
          return
        }
        const playlists = await getMowlPlaylists(abort.signal)
        const songs = await songsAvailability(connections, abort.signal)
        let swedebeat: SwedebeatMixAvailability = { kind: 'off' }
        let mixes: SwedebeatMix[] = []
        // The flag and the user's own "I use Swedebeat" (#525): with either off, no mix choice and no listing.
        if (swedebeatAccess.enabled && swedebeatAccess.usesSwedebeat) {
          try {
            const listed = await getSwedebeatMixes(abort.signal)
            if (listed !== null) {
              swedebeat = { kind: 'ready' }
              mixes = listed
            }
          } catch (error) {
            if (!abort.signal.aborted) {
              swedebeat = { kind: 'failed', message: swedebeatMixListError(error) }
            }
          }
        }
        setSource({ status: 'ready', connection: mowl, playlists, songs, swedebeat, mixes, runningEnabled, soundlessEnabled })
      } catch (error) {
        if (abort.signal.aborted) return
        if (error instanceof ApiError && error.status === 409) {
          setSource({ status: 'no_mowl', reconnect: error.reason !== 'not_connected' })
        } else {
          setSource({ status: 'playlists_failed', message: playlistError(error) })
        }
      }
    })()
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

const LENGTHS = [30, 45, 60, 90] as const

function lengthSentence(t: TFunction, max: number): string {
  return t('newclass.planMaxLength', { count: max })
}

/** The parts of a class the plan leaves out, as one sentence, or null when it leaves out none. */
function leftOut(t: TFunction, names: string[]): string | null {
  if (names.length === 0) return null
  const list =
    names.length === 1
      ? names[0]!
      : t('newclass.list.and', { head: names.slice(0, -1).join(', '), last: names[names.length - 1]! })
  return t('newclass.planLeavesOut', { list })
}

function whole(value: string): number {
  return value.trim() === '' ? Number.NaN : Number(value)
}

function inRange(value: number, min: number, max: number): boolean {
  return Number.isInteger(value) && value >= min && value <= max
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
  // One key per user action: the same form values keep the key, so a repeated click or a retry
  // after a lost answer returns the same job instead of starting a second one.
  const lengthInput = useRef<HTMLInputElement>(null)
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
  const racesOn = allowed?.gates.races ?? true
  const ftpOn = allowed?.gates.ftpTests ?? true
  const gamesOn = allowed?.gates.games ?? true
  const leftOutText = leftOut(t, [
    ...(racesOn ? [] : [t('newclass.planLeavesOut.races')]),
    ...(ftpOn ? [] : [t('newclass.planLeavesOut.ftpTests')]),
  ])
  const attempt = useRef<{ fingerprint: string; key: string } | null>(null)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const brief = {
      length_min: whole(length),
      climbs: running ? 0 : whole(climbs),
      sprints: running ? 0 : whole(sprints),
      races: running || !racesOn ? 0 : whole(races),
    }
    const name = title.trim()
    let invalid: string | null = null
    if (name === '' || name.length > 100) invalid = t('newclass.invalid.name')
    else if (!noMusic && (running || music === 'playlist') && playlist === '') invalid = t('newclass.invalid.playlist')
    else if (!running && !noMusic && music === 'swedebeat_mix' && mixId === '') invalid = t('newclass.invalid.mix')
    else if (!inRange(brief.length_min, 10, 180)) invalid = t('newclass.invalid.length')
    else if (allowed !== null && brief.length_min > allowed.maxMinutes) invalid = lengthSentence(t, allowed.maxMinutes)
    else if (!inRange(brief.climbs, 0, 10)) invalid = t('newclass.invalid.climbs')
    else if (!inRange(brief.sprints, 0, 20)) invalid = t('newclass.invalid.sprints')
    else if (!inRange(brief.races, 0, 5)) invalid = t('newclass.invalid.races')
    else if (style.length > 60) invalid = t('newclass.invalid.style')
    else if (notes.length > 2000) invalid = t('newclass.invalid.notes')
    // A running brief names its sport and has none of the bike-only knobs (the backend refuses them).
    const fullBrief: Brief | RunningBrief | null =
      invalid !== null
        ? null
        : running
          ? { sport: 'running', length_min: brief.length_min, intensity, style, text: notes }
          : {
              ...brief,
              intensity,
              ftp_test: ftp === '' || !ftpOn ? null : (Number(ftp) as 3 | 5 | 20),
              style,
              text: notes,
            }
    // The limit is in bytes (the brief is stored as jsonb), not characters.
    if (invalid === null && fullBrief !== null && briefBytes(fullBrief) > MAX_BRIEF_BYTES) {
      invalid = t('newclass.invalid.briefTooLong')
    }
    setProblem(invalid)
    if (invalid !== null || fullBrief === null) return
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

  const lengthNumber = whole(length)
  const effort = EFFORTS.find((e) => e.value === intensity)
  const chosenPlaylist = playlists.find((p) => p.spotifyPlaylistId === playlist)
  const chosenMix = mixes.find((m) => m.mixId === mixId)
  const picking = !running && !noMusic && music === 'pick' && songs.kind === 'ready'
  const onSwedebeatMix = !running && !noMusic && music === 'swedebeat_mix' && swedebeat.kind === 'ready'
  const summary = [
    {
      label: t('newclass.summary.length'),
      value: Number.isInteger(lengthNumber) ? t('newclass.minutes', { count: lengthNumber }) : t('newclass.notSet'),
    },
    { label: t('newclass.effort'), value: effort === undefined ? '' : t(effort.labelKey) },
    ...(running
      ? [{ label: t('newclass.sport'), value: t('newclass.sport.running') }]
      : [
          { label: t('newclass.climbs'), value: climbs },
          { label: t('newclass.sprints'), value: sprints },
          { label: t('newclass.races'), value: racesOn ? races : '0' },
        ]),
    {
      label: t('newclass.summary.music'),
      value: noMusic
        ? t('newclass.summary.noMusic')
        : picking
        ? t('newclass.summary.songsPicked')
        : onSwedebeatMix
          ? chosenMix
            ? chosenMix.name || t('newclass.untitledMix')
            : t('newclass.summary.noMix')
          : chosenPlaylist
            ? chosenPlaylist.name || t('newclass.untitledPlaylist')
            : t('newclass.summary.noPlaylist'),
    },
  ]
  const profile = sketchProfile({
    length: lengthNumber,
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
        <p className="m-0 text-muted">
          {t('newclass.noPlaylists.body')}
        </p>
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
        {problem ? <Notice kind="error">{problem}</Notice> : null}
        {action.error ? <Notice kind="error">{action.error}</Notice> : null}
        <Card className={section}>
          <CardTitle className="mb-0">{t('newclass.basics')}</CardTitle>
          {runningEnabled ? (
            <div className="flex flex-col gap-2">
              <span id="sport-label" className="text-[0.9375rem] font-medium text-text">
                {t('newclass.sport')}
              </span>
              <div role="group" aria-labelledby="sport-label" className="flex flex-wrap gap-2">
                <ChipButton
                  pressed={!running}
                  onClick={() => {
                    setSport('cycling')
                  }}
                >
                  {t('newclass.sport.cycling')}
                </ChipButton>
                <ChipButton
                  pressed={running}
                  onClick={() => {
                    setSport('running')
                    setMusic('playlist')
                  }}
                >
                  {t('newclass.sport.running')}
                </ChipButton>
              </div>
              {running ? (
                <p className="m-0 text-[0.9375rem] text-muted">
                  {t('newclass.sport.runningHint')}
                </p>
              ) : null}
            </div>
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
          <div className="flex flex-col gap-2">
            <span className="text-[0.9375rem] font-medium text-text">{t('newclass.classLength')}</span>
            <div role="group" aria-label={t('newclass.commonLengths')} className="flex flex-wrap gap-2">
              {LENGTHS.map((m) => (
                <ChipButton
                  key={m}
                  pressed={length === String(m)}
                  disabled={allowed !== null && m > allowed.maxMinutes}
                  className="disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-raised"
                  onClick={() => {
                    setLength(String(m))
                  }}
                >
                  {t('newclass.minutes', { count: m })}
                </ChipButton>
              ))}
              <ChipButton
                pressed={!LENGTHS.some((m) => String(m) === length)}
                onClick={() => {
                  lengthInput.current?.focus()
                }}
              >
                {t('newclass.otherLength')}
              </ChipButton>
            </div>
            <TextInput
              ref={lengthInput}
              label={t('newclass.lengthMinutes')}
              type="number"
              inputMode="numeric"
              min={10}
              max={allowed === null ? 180 : Math.min(180, allowed.maxMinutes)}
              hint={allowed === null ? undefined : lengthSentence(t, allowed.maxMinutes)}
              value={length}
              onChange={(e) => {
                setLength(e.target.value)
              }}
            />
          </div>
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
          {!noMusic && (running || music === 'playlist') ? <PlaylistTiles playlists={playlists} value={playlist} onChange={setPlaylist} covers={covers} /> : null}
          {onSwedebeatMix ? <MixTiles mixes={mixes} value={mixId} onChange={setMixId} /> : null}
          <StylePicker value={style} onChange={setStyle} />
        </Card>
        <Card className={section}>
          <CardTitle className="mb-0">{running ? t('newclass.theRun') : t('newclass.theRide')}</CardTitle>
          <EffortCards value={intensity} onChange={setIntensity} />
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
      <div className="lg:sticky lg:top-4">
        <SideCard profile={profile} summary={summary} busy={action.busy} classesLeft={classesLeft} />
      </div>
    </form>
  )
}
