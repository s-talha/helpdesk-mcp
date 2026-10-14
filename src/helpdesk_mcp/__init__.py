"""A helpdesk exposed over the Model Context Protocol, plus an agent that uses it."""

from .server import build_server
from .store import HelpdeskStore

__all__ = ["HelpdeskStore", "build_server"]
__version__ = "0.1.0"
