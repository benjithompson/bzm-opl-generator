"""bzm-opl-gen."""

from importlib.metadata import PackageNotFoundError, version as _version

try:
    # Read from the installed distribution rather than written here: a literal
    # in this file was already a version behind pyproject.toml, and nothing
    # noticed, because nothing but the MCP handshake reads it.
    __version__ = _version("bzm-opl-gen")
except PackageNotFoundError:
    # A source tree run in place, without even an editable install. Not a
    # reason to fail to import -- the number is a label on a handshake.
    __version__ = "0+unknown"
