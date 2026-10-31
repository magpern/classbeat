import { Link } from '../../router/Router'
import { ChipButton } from '../ui/chip'

export type Music = 'playlist' | 'pick'

/** What "Let Claude pick the songs" needs (ADR 0008 section 9). `off`: the feature is hidden. */
export type SongsAvailability =
  | { kind: 'off' }
  | { kind: 'needs_spotify' }
  | { kind: 'needs_consent' }
  | { kind: 'ready'; spotifyConnectionId: string }

/**
 * The music choice of the new-class form: a playlist from MOWL, or "Let Claude pick the songs".
 * Without Spotify and the working-playlist consent the second choice is not selectable and links
 * to the wizard; with song picking switched off nothing is shown.
 */
export function MusicChoice({
  songs,
  value,
  onChange,
}: {
  songs: SongsAvailability
  value: Music
  onChange: (music: Music) => void
}) {
  if (songs.kind === 'off') return null
  const ready = songs.kind === 'ready'
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
          A playlist from MOWL
        </ChipButton>
        <ChipButton
          pressed={value === 'pick'}
          disabled={!ready}
          onClick={() => {
            onChange('pick')
          }}
        >
          Let Claude pick the songs
        </ChipButton>
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
      {ready && value === 'pick' ? (
        <p className="m-0 text-[0.9375rem] text-muted">
          Claude suggests songs, checks them against MOWL’s BPM and length, and shows you the list.
          Candidates go into a private working playlist in your Spotify; the playlist for your class
          is created only after you approve the songs.
        </p>
      ) : null}
    </div>
  )
}
