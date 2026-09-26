"""wc3-slop-lan's harness: real Warcraft III clients on this machine, joined to the host, driven
and read back by tests, the command line (slop) or an MCP client. See docs/harness.md."""
from .config import Config, ConfigError, load
from .session import NeedsLibrary, Pending, Session, TestFailed

__all__ = ['Config', 'ConfigError', 'load', 'NeedsLibrary', 'Pending', 'Session', 'TestFailed']
