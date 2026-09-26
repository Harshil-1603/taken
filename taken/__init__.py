"""taken: check if a GitHub issue is already taken before you volunteer."""

try:
    from importlib.metadata import PackageNotFoundError, version

    __version__ = version("taken-gh")
except (ImportError, PackageNotFoundError):
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
