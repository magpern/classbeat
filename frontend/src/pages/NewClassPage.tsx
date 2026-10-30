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
import { SelectField, TextAreaField } from '../components/classes/fields'
import { Card, Field, Notice, useAction } from '../components/forms'
import { RequireAuth } from '../components/RequireAuth'
import { BRIEF_TOO_LONG, briefBytes, MAX_BRIEF_BYTES } from '../lib/brief'
import { newIdempotencyKey, rememberInput } from '../lib/submittedInput'
import { Link, useRouter } from '../router/Router'

type Source =
  | { status: 'loading' }
  | { status: 'no_mowl'; reconnect: boolean }
  | { status: 'playlists_failed'; message: string }
  | { status: 'ready'; connection: Connection; playlists: Playlist[] }

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
  quota: 'You have used up what your plan allows for now.',
  class_busy: 'This class already has a design running.',
  busy: 'You already have designs running. Wait for one to finish, then try again.',
  idempotency_mismatch: 'That did not work. Check the form and try again.',
  invalid_input: `The class was not accepted. Check the fields; the style and description together may be over ${String(MAX_BRIEF_BYTES)} bytes.`,
}

function createError(error: unknown): string {
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
        setSource({ status: 'ready', connection: mowl, playlists })
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
    <div className="stack">
      <h1>Design a class</h1>
      {source.status === 'loading' ? <p>Loading your MOWL music</p> : null}
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
          <p>
            <button
              type="button"
              className="button"
              onClick={() => {
                setSource({ status: 'loading' })
                setReload((n) => n + 1)
              }}
            >
              Try again
            </button>
          </p>
        </>
      ) : null}
      {source.status === 'ready' ? (
        <ClassForm connection={source.connection} playlists={source.playlists} />
      ) : null}
    </div>
  )
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
}: {
  connection: Connection
  playlists: Playlist[]
}) {
  const { navigate } = useRouter()
  const action = useAction()
  const [title, setTitle] = useState('')
  const [playlist, setPlaylist] = useState('')
  const [length, setLength] = useState('45')
  const [intensity, setIntensity] = useState<Brief['intensity']>('moderate')
  const [climbs, setClimbs] = useState('0')
  const [sprints, setSprints] = useState('0')
  const [races, setRaces] = useState('0')
  const [ftp, setFtp] = useState('')
  const [style, setStyle] = useState('')
  const [notes, setNotes] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  // One key per user action: the same form values keep the key, so a repeated click or a retry
  // after a lost answer returns the same job instead of starting a second one.
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
    else if (playlist === '') invalid = 'Choose a playlist.'
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

  if (playlists.length === 0) {
    return (
      <Card title="No playlists found" wide>
        <p>
          MOWL lists no Spotify playlists for your account. Add one in the Intelligent Cycling app,
          then reload this page.
        </p>
      </Card>
    )
  }
  return (
    <form className="panel" onSubmit={submit} noValidate aria-label="New class">
      {problem ? <Notice kind="error">{problem}</Notice> : null}
      {action.error ? <Notice kind="error">{action.error}</Notice> : null}
      <Field
        label="Class name"
        value={title}
        maxLength={100}
        onChange={(e) => {
          setTitle(e.target.value)
        }}
        required
      />
      <SelectField
        label="Playlist"
        value={playlist}
        onChange={(e) => {
          setPlaylist(e.target.value)
        }}
        hint="classbeat adds this playlist to your MOWL music so it can read the songs. It stays private to you."
        required
      >
        <option value="">Choose a playlist</option>
        {playlists.map((p) => (
          <option key={p.spotifyPlaylistId} value={p.spotifyPlaylistId}>
            {p.name || 'Untitled playlist'}
          </option>
        ))}
      </SelectField>
      <Field
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
      <SelectField
        label="Intensity"
        value={intensity}
        onChange={(e) => {
          setIntensity(e.target.value as Brief['intensity'])
        }}
      >
        <option value="easy">Easy</option>
        <option value="moderate">Moderate</option>
        <option value="hard">Hard</option>
      </SelectField>
      <Field
        label="Climbs"
        type="number"
        inputMode="numeric"
        min={0}
        max={10}
        value={climbs}
        onChange={(e) => {
          setClimbs(e.target.value)
        }}
      />
      <Field
        label="Sprints"
        type="number"
        inputMode="numeric"
        min={0}
        max={20}
        value={sprints}
        onChange={(e) => {
          setSprints(e.target.value)
        }}
      />
      <Field
        label="Races"
        type="number"
        inputMode="numeric"
        min={0}
        max={5}
        value={races}
        onChange={(e) => {
          setRaces(e.target.value)
        }}
      />
      <SelectField
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
      </SelectField>
      <Field
        label="Style"
        value={style}
        maxLength={60}
        hint="For example: hills, intervals, endurance."
        onChange={(e) => {
          setStyle(e.target.value)
        }}
      />
      <TextAreaField
        label="Anything else"
        value={notes}
        maxLength={2000}
        hint="Tell classbeat what the class should feel like."
        onChange={(e) => {
          setNotes(e.target.value)
        }}
      />
      <button className="button" type="submit" disabled={action.busy}>
        Design the class
      </button>
    </form>
  )
}
