// The source catalogue (ADR 0023 section 3): English, keys by feature. Other languages
// are typed as a partial `Catalogue`; a missing key shows this text. A value is a string with
// named `{name}` placeholders, or plural forms chosen by the `count` parameter.
// No markup in a string: links and emphasis are slots of `Trans`.

export const en = {
  "language.label": "Language",
  "language.follow": "Follow this browser",
  "language.help":
    'The language of this site. "Follow this browser" uses your browser\'s language on each device; email is sent in English until more languages are available.',
  "format.soonUnknown": "a few minutes from now",
  "usage.underOneCent": "under {amount}",
  // Server sentences by code (ADR 0023 section 6): the text equals the backend's English; the
  // page words a code from here and shows the server's text for a code it does not have.
  'sentence.spotify.invalid_grant': 'Spotify no longer accepts the connection. Reconnect Spotify.',
  'sentence.spotify.app_misconfigured': 'Spotify does not recognise this app setup. Check the Client ID and the redirect URI in your Spotify app.',
  'sentence.spotify.not_allowlisted': "Spotify refused this account. In your Spotify app's User Management add this account's name and email (Development Mode allows 5 people), and make sure the app's owner has Spotify Premium.",
  'sentence.spotify.forbidden': 'Spotify refused this request for your account or playlist.',
  'sentence.spotify.rate_limited': { one: 'Spotify asks to slow down; trying again in about {count} minute.', other: 'Spotify asks to slow down; trying again in about {count} minutes.' },
  'sentence.spotify.quota_exhausted': { one: 'Your Spotify app has used up its allowance for now. Try again in about {count} hour.', other: 'Your Spotify app has used up its allowance for now. Try again in about {count} hours.' },
  'sentence.spotify.upstream_unavailable': 'Spotify is not answering.',
  'sentence.spotify.not_found': 'Spotify could not find that playlist.',
  'sentence.spotify.scope_mismatch': 'Spotify did not give the access classbeat needs. Connect again and allow it.',
  'sentence.spotify.invalid_response': 'Spotify did not give the access classbeat needs. Connect again and allow it.',
  'sentence.spotify.account_changed': 'This is a different Spotify account than before. Confirm to switch (classbeat forgets the working playlist).',
  'sentence.spotify.access_denied': 'Spotify was not connected.',
  'sentence.spotify.unknown': 'Something went wrong with Spotify. Try again later.',
  'sentence.job.consent_missing': 'Allow the working playlist in your Spotify settings first.',
  'sentence.job.pool_unusable': 'Your working playlist was changed: make it private again or set it up again.',
  'sentence.job.pool_missing': 'Your working playlist was gone from Spotify; trying again by itself.',
  'sentence.job.pool_not_synced': 'MOWL has not caught up with the working playlist; try again in a minute.',
  'sentence.job.playlist_mismatch': "Spotify's playlist did not match the approved songs; nothing was designed. Create it again.",
  'sentence.job.pool_too_small': 'Not enough usable songs were found; try a wider style or era.',
  'sentence.job.account_changed': 'Your Spotify account is not the one these songs were chosen for. Ask for new songs.',
  'sentence.job.pool_busy': 'Another song pick is still running.',
  'sentence.job.media_host_not_allowed': 'The mix was added to your MOWL account, but it could not be analysed: analysing a new Swedebeat mix is not available on this server yet. Mixes analysed before still work.',
  'sentence.job.media_refused': 'The audio of this mix could not be fetched. Nothing was designed.',
  'sentence.job.analysis_failed': 'The audio of this mix could not be analysed. Try another mix.',
  'sentence.job.feature_off': 'This feature is switched off on this server right now. Nothing was designed.',
  'sentence.job.analysis_limit': 'You have analysed 10 new Swedebeat mixes in the last 24 hours. Mixes analysed before still work; try a new one tomorrow.',
  'sentence.job.handoff_expired': 'That check expired. Start again from the Swedebeat step; nothing was fetched.',
  'sentence.swedebeat.wait': { one: 'Swedebeat asks to slow down; trying again in about {count} minute.', other: 'Swedebeat asks to slow down; trying again in about {count} minutes.' },
  'sentence.swedebeat.stopped': 'Swedebeat asks to slow down. This match has stopped.',
} as const satisfies Record<string, Message>;

export type Plural = {
  one?: string;
  other: string;
  zero?: string;
  two?: string;
  few?: string;
  many?: string;
};
export type Message = string | Plural;

export type CatalogueKey = keyof typeof en;
/** What a non-English catalogue may hold: some of the keys, never others. */
export type Catalogue = Partial<Record<CatalogueKey, Message>>;
