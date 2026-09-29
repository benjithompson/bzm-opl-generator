"""bzm-opl-gen."""

from importlib.metadata import PackageNotFoundError, version as _version

try:
    # From the installed distribution, so it cannot drift from pyproject.toml.
    __version__ = _version("bzm-opl-gen")
except PackageNotFoundError:
    # A source tree run in place; the number only labels the MCP handshake.
    __version__ = "0+unknown"
