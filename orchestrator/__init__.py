# orchestrator package
import sys as _sys

# Windows consoles often use cp1252, which cannot encode the arrows and
# dashes the CLIs print (or text quoted from model findings). Replace such
# characters instead of crashing after the real work is done.
for _stream in (_sys.stdout, _sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="backslashreplace")
        except (ValueError, OSError):
            pass


import re as _re

_SAFE_RUN_ID = _re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def safe_run_id(run_id: str) -> str:
    """Return run_id unchanged, or raise ValueError if it could escape runs/."""
    if not isinstance(run_id, str) or not _SAFE_RUN_ID.fullmatch(run_id):
        raise ValueError(f"invalid run id {run_id!r}: use letters, digits, '-' or '_' only")
    return run_id
