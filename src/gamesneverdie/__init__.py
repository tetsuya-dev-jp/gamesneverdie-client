"""Unofficial, on-demand access to Games Never Die's public game pages."""

from .client import Client, FetchError, NotFoundError
from .parsing import FIELDS, ParseError

__all__ = ["FIELDS", "Client", "FetchError", "NotFoundError", "ParseError"]
__version__ = "0.1.0"
