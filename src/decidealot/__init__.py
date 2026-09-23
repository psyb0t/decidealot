"""Decidealot local System One decision service."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("decidealot")
except PackageNotFoundError:
    __version__ = "0.0.0+source"
