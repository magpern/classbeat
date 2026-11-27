// The Spotify listening preview (ADR 0042, CP-S3): audition a Spotify class on the person's own
// Spotify app, the states of the cursor, every failure in words, and the timing corrections.

export const previewEn = {
  'preview.title': 'Listen on Spotify',
  'preview.intro':
    'Play the class songs on your own Spotify app and follow where you are. Spotify plays one song at a time, so a crossfade is not heard here. Nothing plays until you press Play.',
  'preview.loading': 'Checking the listening preview',
  'preview.loadFailed': 'The listening preview could not be loaded. Try again in a moment.',

  // The cursor states.
  'preview.state.idle': 'Nothing is playing from here. Press Play on a song, or click the timeline.',
  'preview.state.ended': 'Nothing is playing any more. Press Play to listen again.',
  'preview.state.starting': 'Starting on your Spotify app.',
  'preview.state.playing': 'Playing on {device}.',
  'preview.state.paused': 'Paused on {device}.',
  'preview.state.external':
    'Something else is playing on your Spotify app, so there is no cursor. Press Play here to listen to the class again.',
  'preview.state.unaligned':
    'This song’s timing against Spotify’s recording is not reliable, so the cursor is not shown. It still plays.',
  'preview.state.outside_before':
    'Spotify is playing the part before this class starts (an intro), so there is no cursor yet.',
  'preview.state.outside_after':
    'Spotify is playing past the part of the song this class uses, so there is no cursor.',
  'preview.state.seek': 'The position jumped (you or Spotify skipped). The cursor follows it.',
  'preview.state.device': 'Now playing on {device}. The cursor follows.',
  'preview.state.device.none': 'your Spotify app',

  // The cursor and its band.
  'preview.cursor.none': 'No cursor.',
  'preview.cursor.at': 'Cursor at {time}, about ±{ms} ms.',
  'preview.cursor.overlap': 'Inside an overlap of two songs: the cursor is only approximate, about ±{ms} ms.',
  'preview.timeline.label': 'The class by song. Overlaps are hatched.',
  'preview.timeline.song': 'Song {n}: {title}. Press Enter to play it from its start.',
  'preview.timeline.overlap': 'Overlap of song {a} and song {b}',
  'preview.play': 'Play',
  'preview.playSong': 'Play song {n}',
  'preview.pause': 'Pause',

  // Choosing a song inside an overlap.
  'preview.lane.title': 'Two songs overlap here. Which one do you want to hear?',
  'preview.lane.incoming': 'The incoming song ({title})',
  'preview.lane.outgoing': 'The outgoing song ({title})',
  'preview.lane.cancel': 'Cancel',

  // Ways forward for a failure.
  'preview.action.reconnect': 'Reconnect Spotify',
  'preview.action.open_spotify': 'Open Spotify, then press Play again.',
  'preview.action.sign_in': 'Sign in again',
  'preview.action.reload': 'Reload the page',

  // Failures, in words. No code is shown.
  'preview.err.not_connected': 'Spotify is not connected. Connect it to listen here.',
  'preview.err.needs_reconnect': 'Spotify needs to be connected again before it can play here.',
  'preview.err.preview_not_granted':
    'The listening preview is not switched on for your Spotify connection. Reconnect Spotify and allow playback control.',
  'preview.err.premium_or_allowlist':
    'Spotify refused to play. Playback control needs Spotify Premium, and while your Spotify app is in development mode your name and email must be under its User Management.',
  'preview.err.no_device':
    'No Spotify app is open to play on. Open Spotify on your phone or computer, then press Play again.',
  'preview.err.rate_limited': 'Spotify asks to wait a little before the next request. Try again in a moment.',
  'preview.err.rate_limited_for': 'Spotify asks to wait. Try again in {seconds} seconds.',
  'preview.err.upstream_unavailable': 'Spotify is not answering right now. Try again in a moment.',
  'preview.err.invalid_response': 'Spotify gave an answer that could not be used. Try again in a moment.',
  'preview.err.network': 'The connection was lost. Check your internet and try again.',
  'preview.err.unauthorized': 'You were signed out. Sign in again to listen.',
  'preview.err.no_draft': 'This class has no version with Spotify songs to listen to. Reload the page.',
  'preview.err.not_found': 'This class was not found.',
  'preview.err.out_of_range': 'That position is outside the song.',
  'preview.err.stale_base': 'The class changed while this page was open. Reload the page and try again.',
  'preview.err.no_analysis': 'This song has not been analysed, so its timing cannot be corrected.',
  'preview.err.correction_limit': 'There are too many timing corrections for this class.',
  'preview.err.invalid_input': 'That correction is not allowed. It must be within a minute either way.',
  'preview.err.generic': 'Something went wrong. Try again in a moment.',

  // Timing corrections.
  'preview.corr.title': 'Fine-tune the timing of song {n}',
  'preview.corr.intro':
    'If the cursor is ahead of the music you hear, press +. If it is behind, press −. A correction only moves the cursor here: the class does not change.',
  'preview.corr.pick': 'Song',
  'preview.corr.value': 'Your correction: {value} s',
  'preview.corr.none': 'No correction.',
  'preview.corr.step': 'Move by {delta} s',
  'preview.corr.listenAgain':
    'Listen again: this correction was set before the music was analysed again. It is shown here but not used until you confirm it.',
  'preview.corr.confirm': 'Confirm this correction',
  'preview.corr.clear': 'Clear the correction',
  'preview.corr.saving': 'Saving',
  'preview.corr.saved': 'Saved.',
  'preview.prov.title': 'Where the timing of song {n} comes from',
  'preview.prov.note': 'classbeat’s analysis and your correction are kept apart and never added into one number.',
} as const

/** The key of a sentence of the listening preview. */
export type PreviewKey = keyof typeof previewEn
