import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ApiError } from '../api/client'
import { getConnections, type Connection } from '../api/connections'
import {
  createDesignJob,
  getMowlPlaylists,
  type Brief,
  type DesignInput,
  type Playlist,
} from '../api/jobs'
import { errorMessage } from '../api/messages'
import { startError } from '../api/songMessages'
import { createPickJob, type PickInput } from '../api/songs'
import { getPlan } from '../api/plan'
import { getSpotifyStatus } from '../api/spotify'
import { MusicChoice, type Music, type SongsAvailability } from '../components/songs/MusicChoice'
import { EffortCards } from '../components/newclass/EffortCards'
import { SelectInput, TextArea, TextInput } from '../components/newclass/inputs'
import { PlaylistTiles } from '../components/newclass/PlaylistTiles'
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
import { BRIEF_TOO_LONG, briefBytes, MAX_BRIEF_BYTES } from '../lib/brief'
import { newIdempotencyKey, rememberInput, rememberPick } from '../lib/submittedInput'
import { readPreset } from '../lib/presets'
import { Link, useQuery, useRouter } from '../router/Router'

type Source =
  | { status: 'loading' }
  | { status: 'no_mowl'; reconnect: boolean }
  | { status: 'playlists_failed'; message: string }
  | { status: 'ready'; connection: Connection; playlists: Playlist[]; songs: SongsAvailability }

const PLAYLIST_ERRORS: Record<string, string> = {
  upstream_unavailable: 'MOWL could not be reached. Try again in a few minutes.',
  key_unavailable: 'MOWL could not be reached. Try again in a few minutes.',
  rate_limited: 'MOWL asked classbeat to slow down. Try again in a few minutes.',
}

function playlistError(error: unknown): string {
  if (error instanceof ApiError && error.reason !== null && error.status !== 409) {
    const known = Object.hasOwn(PLAYLIST_ERRORS, error.reason) ? PLAYLIST_ERRORS[error.reason] : undefined
    if (known !== undefined) return known
  }
  return 'Your playlists could not be loaded from MOWL. Try again in a few minutes.'
}

const CREATE_ERRORS: Record<string, string> = {
  not_connected: 'MOWL is not connected. Connect it on your account page first.',
  class_busy: 'This class already has a design running.',
  idempotency_mismatch: 'That did not work. Check the form and try again.',
  invalid_input: `The class was not accepted. Check the fields; the style and description together may be over ${String(MAX_BRIEF_BYTES)} bytes.`,
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

function createError(error: unknown): string {
  const refusal = planRefusalText(error)
  if (refusal !== null) return refusal
  if (error instanceof ApiError && error.reason !== null) {
    const known = Object.hasOwn(CREATE_ERRORS, error.reason) ? CREATE_ERRORS[error.reason] : undefined
    if (known !== undefined) return known
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
  const [source, setSource] = useState<Source>({ status: 'loading' })
  const [reload, setReload] = useState(0)

  useEffect(() => {
    const abort = new AbortController()
    void (async () => {
      try {
        const connections = await getConnections()
        const mowl = connections.find((c) => c.provider === 'mowl')
        if (mowl === undefined || mowl.status !== 'connected') {
          setSource({ status: 'no_mowl', reconnect: mowl !== undefined })
          return
        }
        const playlists = await getMowlPlaylists(abort.signal)
        const songs = await songsAvailability(connections, abort.signal)
        setSource({ status: 'ready', connection: mowl, playlists, songs })
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
      <h1 className="m-0 font-display text-3xl font-extrabold">Design a class</h1>
      {source.status === 'loading' ? <p className="m-0 text-muted">Loading your MOWL music</p> : null}
      {source.status === 'no_mowl' ? (
        <Notice>
          {source.reconnect
            ? 'MOWL no longer accepts the stored sign-in. '
            : 'classbeat reads your music from MOWL. '}
          <Link to="/account">{source.reconnect ? 'Connect MOWL again' : 'Connect MOWL on your account page'}</Link>
          , then come back here.
        </Notice>
      ) : null}
      {source.status === 'playlists_failed' ? (
        <>
          <Notice kind="error">{source.message}</Notice>
          <p className="m-0">
            <Button
              variant="secondary"
              onClick={() => {
                setSource({ status: 'loading' })
                setReload((n) => n + 1)
              }}
            >
              Try again
            </Button>
          </p>
        </>
      ) : null}
      {source.status === 'ready' ? (
        <ClassForm connection={source.connection} playlists={source.playlists} songs={source.songs} />
      ) : null}
    </div>
  )
}

const LENGTHS = [30, 45, 60, 90] as const

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
}: {
  connection: Connection
  playlists: Playlist[]
  songs: SongsAvailability
}) {
  const { navigate } = useRouter()
  const preset = readPreset(useQuery())
  const action = useAction()
  const [title, setTitle] = useState('')
  const [playlist, setPlaylist] = useState('')
  const [music, setMusic] = useState<Music>('playlist')
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
  useEffect(() => {
    const abort = new AbortController()
    getPlan(abort.signal).then(
      (plan) => {
        if (!abort.signal.aborted) {
          setClassesLeft(Math.max(0, plan.limits.classesPerMonth - plan.classesHeld))
        }
      },
      () => undefined,
    )
    return () => {
      abort.abort()
    }
  }, [])
  const attempt = useRef<{ fingerprint: string; key: string } | null>(null)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const brief = {
      length_min: whole(length),
      climbs: whole(climbs),
      sprints: whole(sprints),
      races: whole(races),
    }
    const name = title.trim()
    let invalid: string | null = null
    if (name === '' || name.length > 100) invalid = 'Give the class a name of up to 100 characters.'
    else if (music === 'playlist' && playlist === '') invalid = 'Choose a playlist.'
    else if (!inRange(brief.length_min, 10, 180)) invalid = 'The length is 10 to 180 minutes.'
    else if (!inRange(brief.climbs, 0, 10)) invalid = 'Climbs are 0 to 10.'
    else if (!inRange(brief.sprints, 0, 20)) invalid = 'Sprints are 0 to 20.'
    else if (!inRange(brief.races, 0, 5)) invalid = 'Races are 0 to 5.'
    else if (style.length > 60) invalid = 'The style is up to 60 characters.'
    else if (notes.length > 2000) invalid = 'The description is up to 2000 characters.'
    const fullBrief: Brief | null =
      invalid !== null
        ? null
        : {
            ...brief,
            intensity,
            ftp_test: ftp === '' ? null : (Number(ftp) as 3 | 5 | 20),
            style,
            text: notes,
          }
    // The limit is in bytes (the brief is stored as jsonb), not characters.
    if (invalid === null && fullBrief !== null && briefBytes(fullBrief) > MAX_BRIEF_BYTES) {
      invalid = BRIEF_TOO_LONG
    }
    setProblem(invalid)
    if (invalid !== null || fullBrief === null) return
    if (music === 'pick' && songs.kind === 'ready') {
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
    const input: DesignInput = {
      title: name,
      brief: fullBrief,
      music: { source: 'mowl_spotify_playlist', spotify_playlist_id: playlist },
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
    }, createError)
  }

  const lengthNumber = whole(length)
  const effort = EFFORTS.find((e) => e.value === intensity)
  const chosenPlaylist = playlists.find((p) => p.spotifyPlaylistId === playlist)
  const picking = music === 'pick' && songs.kind === 'ready'
  const summary = [
    { label: 'Length', value: Number.isInteger(lengthNumber) ? `${String(lengthNumber)} min` : 'Not set' },
    { label: 'Effort', value: effort ? `${effort.label}, zones ${String(effort.low)} to ${String(effort.high)}` : '' },
    { label: 'Climbs', value: climbs },
    { label: 'Sprints', value: sprints },
    { label: 'Races', value: races },
    {
      label: 'Music',
      value: picking
        ? 'Songs picked for you'
        : chosenPlaylist
          ? chosenPlaylist.name || 'Untitled playlist'
          : 'No playlist chosen yet',
    },
  ]
  const profile = sketchProfile({
    length: lengthNumber,
    effort: intensity,
    climbs: whole(climbs),
    sprints: whole(sprints),
    races: whole(races),
  })

  if (playlists.length === 0 && songs.kind !== 'ready') {
    return (
      <Card>
        <CardTitle>No playlists found</CardTitle>
        <p className="m-0 text-muted">
          MOWL lists no Spotify playlists for your account. Add one in the Intelligent Cycling app,
          then reload this page.
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
      aria-label="New class"
    >
      <div className="flex min-w-0 flex-col gap-5">
        {problem ? <Notice kind="error">{problem}</Notice> : null}
        {action.error ? <Notice kind="error">{action.error}</Notice> : null}
        <Card className={section}>
          <CardTitle className="mb-0">The basics</CardTitle>
          <TextInput
            label="Class name"
            value={title}
            maxLength={100}
            onChange={(e) => {
              setTitle(e.target.value)
            }}
            required
          />
          <div className="flex flex-col gap-2">
            <span className="text-[0.9375rem] font-medium text-text">Class length</span>
            <div role="group" aria-label="Common lengths" className="flex flex-wrap gap-2">
              {LENGTHS.map((m) => (
                <ChipButton
                  key={m}
                  pressed={length === String(m)}
                  onClick={() => {
                    setLength(String(m))
                  }}
                >
                  {String(m)} min
                </ChipButton>
              ))}
              <ChipButton
                pressed={!LENGTHS.some((m) => String(m) === length)}
                onClick={() => {
                  lengthInput.current?.focus()
                }}
              >
                Other length
              </ChipButton>
            </div>
            <TextInput
              ref={lengthInput}
              label="Length in minutes"
              type="number"
              inputMode="numeric"
              min={10}
              max={180}
              value={length}
              onChange={(e) => {
                setLength(e.target.value)
              }}
            />
          </div>
        </Card>
        <Card className={section}>
          <CardTitle className="mb-0">Music</CardTitle>
          <MusicChoice songs={songs} value={music} onChange={setMusic} />
          {music === 'playlist' ? <PlaylistTiles playlists={playlists} value={playlist} onChange={setPlaylist} /> : null}
          <StylePicker value={style} onChange={setStyle} />
        </Card>
        <Card className={section}>
          <CardTitle className="mb-0">The ride</CardTitle>
          <EffortCards value={intensity} onChange={setIntensity} />
          <div className="grid gap-4 sm:grid-cols-3">
            <Stepper label="Climbs" noun="climbs" value={climbs} min={0} max={10} onChange={setClimbs} />
            <Stepper label="Sprints" noun="sprints" value={sprints} min={0} max={20} onChange={setSprints} />
            <Stepper label="Races" noun="races" value={races} min={0} max={5} onChange={setRaces} />
          </div>
          <SelectInput
            label="FTP test"
            value={ftp}
            onChange={(e) => {
              setFtp(e.target.value)
            }}
          >
            <option value="">None</option>
            <option value="3">3 minutes</option>
            <option value="5">5 minutes</option>
            <option value="20">20 minutes</option>
          </SelectInput>
        </Card>
        <Card className={section}>
          <TextArea
            label="Anything else?"
            value={notes}
            maxLength={2000}
            hint="Tell classbeat what the class should feel like."
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
