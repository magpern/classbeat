"""classbeat backend: web API and worker."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("classbeat")
except PackageNotFoundError:  # pragma: no cover - only without an installed package
    __version__ = "0+unknown"
