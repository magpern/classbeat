/**
 * Says that the songs, artists and covers come from Spotify (Spotify's Design & Branding
 * Guidelines for developers, "Attribution"). The official Spotify logo is a brand asset that is
 * downloaded from Spotify's brand resources and bundled; until it is added here this is the
 * text form, never an imitation of the logo.
 */
export function SpotifyAttribution({ className }: { className?: string }) {
  return (
    <p className={className ?? 'm-0 text-[0.8125rem] text-muted'} data-testid="spotify-attribution">
      Song information and covers from Spotify
    </p>
  )
}
