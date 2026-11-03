import { Link } from '../../router/Router'
import { ChipButton } from '../ui/chip'

export type Music = 'playlist' | 'pick' | 'swedebeat_mix'

/** What "Pick songs for me" needs (ADR 0008 section 9). `off`: the feature is hidden. */
export type SongsAvailability =
  | { kind: 'off' }
  | { kind: 'needs_spotify' }
  | { kind: 'needs_consent' }
  | { kind: 'ready'; spotifyConnectionId: string }

/**
 * Whether "Use my Swedebeat mix" is offered (ADR 0016 section 3). The new-class page renders the form
 * only with a connected MOWL account, so there is no "needs MOWL" state here.
 */
export type SwedebeatMixAvailability =
  | { kind: 'off' }
  | { kind: 'ready' }
  | { kind: 'failed'; message: string }

/**
 * The music choice of the new-class form: a playlist from MOWL, song picking, or a Swedebeat mix.
 * Without Spotify and the working-playlist consent the second choice is not selectable and links
 * to the wizard; with song picking switched off nothing is shown. The Swedebeat mix choice needs
 * the effective `swedebeat_enabled` flag (the page itself needs the MOWL connection).
 */
export function MusicChoice({
  songs,
  swedebeat,
  value,
  onChange,
}: {
  songs: SongsAvailability
  swedebeat: SwedebeatMixAvailability
  value: Music
  onChange: (music: Music) => void
}) {
  if (songs.kind === 'off' && swedebeat.kind === 'off') return null
  const ready = songs.kind === 'ready'
  const swedebeatReady = swedebeat.kind === 'ready'
  return (
    <div className="flex flex-col gap-2" role="group" aria-labelledby="music-label">
      <span id="music-label" className="text-[0.9375rem] font-medium text-text">
        Music
      </span>
      <div className="flex flex-wrap gap-2">
        <ChipButton
          pressed={value === 'playlist'}
          onClick={() => {
            onChange('playlist')
          }}
        >
          Use my playlist
        </ChipButton>
        {songs.kind !== 'off' ? (
          <ChipButton
            pressed={value === 'pick'}
            disabled={!ready}
            onClick={() => {
              onChange('pick')
            }}
          >
            Pick songs for me
          </ChipButton>
        ) : null}
        {swedebeat.kind !== 'off' ? (
          <ChipButton
            pressed={value === 'swedebeat_mix'}
            disabled={!swedebeatReady}
            onClick={() => {
              onChange('swedebeat_mix')
            }}
          >
            Use my Swedebeat mix
          </ChipButton>
        ) : null}
      </div>
      {songs.kind === 'needs_spotify' ? (
        <p className="m-0 text-[0.9375rem] text-muted">
          Picking songs needs Spotify. <Link to="/account/spotify">Connect Spotify</Link> first.
        </p>
      ) : null}
      {songs.kind === 'needs_consent' ? (
        <p className="m-0 text-[0.9375rem] text-muted">
          Allow the working playlist in your Spotify settings first.{' '}
          <Link to="/account/spotify">Open the Spotify settings</Link>
        </p>
      ) : null}
      {swedebeat.kind === 'failed' ? (
        <p className="m-0 text-[0.9375rem] text-muted" role="status">
          {swedebeat.message}
        </p>
      ) : null}
      {ready && value === 'pick' ? (
        <p className="m-0 text-[0.9375rem] text-muted">
          Claude suggests songs, checks them against MOWL’s BPM and length, and shows you the list.
          Candidates go into a private working playlist in your Spotify; the playlist for your class
          is created only after you approve the songs.
        </p>
      ) : null}
      {swedebeatReady && value === 'swedebeat_mix' ? (
        <p className="m-0 text-[0.9375rem] text-muted">
          classbeat designs the class to your mix’s songs and beats. You do not need a Swedebeat
          sign-in in classbeat for this; MOWL lists the mixes in your Intelligent Cycling account.
        </p>
      ) : null}
    </div>
  )
}
