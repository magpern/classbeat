"""The Spotify writes' runtime from the settings (the worker and the web app build it alike)."""

from classbeat.config import Settings
from classbeat.playlists.common import Runtime
from classbeat.spotify.http import SpotifyHttp


def playlists_runtime(settings: Settings) -> Runtime:
    return Runtime(
        http=SpotifyHttp(timeout_seconds=settings.spotify_refresh_timeout_seconds),
        margin_seconds=settings.spotify_refresh_margin_seconds,
        listing_lag_seconds=settings.spotify_listing_lag_seconds,
    )
